# Copyright 2020 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os

import numpy as np
from scipy.io import wavfile

import tensorflow.compat.v2 as tf
from tensorflow_examples.lite.model_maker.core.data_util import audio_dataloader
from tensorflow_examples.lite.model_maker.core.task.model_spec import audio_spec


def write_file(root, filepath):
  full_path = os.path.join(root, filepath)
  os.makedirs(os.path.dirname(full_path), exist_ok=True)
  with open(full_path, 'w') as f:
    f.write('<content>')


def write_sample(root,
                 category,
                 file_name,
                 sample_rate,
                 duration_sec,
                 value,
                 dtype=np.int16):
  os.makedirs(os.path.join(root, category), exist_ok=True)
  xs = value * np.ones(shape=(int(sample_rate * duration_sec),), dtype=dtype)
  wavfile.write(os.path.join(root, category, file_name), sample_rate, xs)


class MockSpec(audio_spec.BaseSpec):

  def create_model(self):
    return None

  def run_classifier(self, *args, **kwargs):
    return None


class AudioDataLoaderTest(tf.test.TestCase):

  def _get_folder_path(self, sub_folder_name):
    folder_path = os.path.join(self.get_temp_dir(), sub_folder_name)
    if os.path.exists(folder_path):
      return
    tf.compat.v1.logging.info('Test path: %s', folder_path)
    os.mkdir(folder_path)
    return folder_path

  def test_examples_helper(self):
    root = self._get_folder_path('test_examples_helper')
    write_file(root, 'a/1.wav')
    write_file(root, 'a/2.wav')
    write_file(root, 'b/1.wav')
    write_file(root, 'b/README')  # Ignored
    write_file(root, 'a/b/c/d.wav')  # Ignored
    write_file(root, 'AUTHORS.md')  # Ignored
    write_file(root, 'temp.wav')  # Ignored

    def is_wav_files(name):
      return name.endswith('.wav')

    def fullpath(name):
      return os.path.join(root, name)

    helper = audio_dataloader.ExamplesHelper(root, is_wav_files)
    self.assertEqual(helper.sorted_cateogries, ['a', 'b'])
    self.assertEqual(
        helper.examples_and_labels(),
        ([fullpath('a/1.wav'),
          fullpath('a/2.wav'),
          fullpath('b/1.wav')], ['a', 'a', 'b']))
    self.assertEqual(
        helper.examples_and_label_indices(),
        ([fullpath('a/1.wav'),
          fullpath('a/2.wav'),
          fullpath('b/1.wav')], [0, 0, 1]))

  def test_no_audio_files_found(self):
    folder_path = self._get_folder_path('test_no_audio_files_found')
    write_sample(folder_path, 'unknown', '2s.bak', 44100, 2, value=1)
    with self.assertRaisesRegexp(ValueError, 'No audio files found'):
      spec = MockSpec(model_dir=folder_path)
      audio_dataloader.DataLoader.from_folder(spec, folder_path)

  def test_check_encoding(self):
    folder_path = self._get_folder_path('test_check_encoding')
    write_sample(
        folder_path, 'unknown', '2s.wav', 44100, 2, value=0, dtype=np.uint8)
    with self.assertRaisesRegexp(ValueError, '16 bit PCM'):
      spec = MockSpec(model_dir=folder_path)
      audio_dataloader.DataLoader.from_folder(spec, folder_path)

  def test_from_folder(self):
    folder_path = self._get_folder_path('test_from_folder')
    write_sample(folder_path, 'background', '2s.wav', 44100, 2, value=0)
    write_sample(folder_path, 'command1', '1s.wav', 44100, 1, value=1)
    # Too short, skipped.
    write_sample(folder_path, 'command1', '0.1s.wav', 44100, .1, value=2)
    # Not long enough for 2 files, the remaining .5s will be skipped.
    write_sample(folder_path, 'command2', '1.5s.wav', 44100, 1.5, value=3)
    # Skipped
    write_sample(folder_path, 'command0', '0.1s.wav', 4410, .1, value=4)
    # Resampled
    write_sample(folder_path, 'command0', '1.8s.wav', 4410, 1.8, value=5)
    # Ignored due to wrong file extension
    write_sample(folder_path, 'command0', '1.8s.bak', 4410, 1.8, value=6)

    spec = MockSpec(model_dir=folder_path)
    loader = audio_dataloader.DataLoader.from_folder(spec, folder_path)

    self.assertEqual(len(loader), 5)
    self.assertEqual(loader.index_to_label,
                     ['background', 'command0', 'command1', 'command2'])

    def is_cached(filename):
      path = os.path.join(folder_path, 'cache', filename)
      self.assertTrue(tf.io.gfile.exists(path))
      sampling_rate, _ = wavfile.read(path)
      self.assertEqual(sampling_rate, 44100)

    is_cached('background/2s_0.wav')
    is_cached('background/2s_1.wav')
    is_cached('command1/1s_0.wav')
    is_cached('command2/1.5s_0.wav')
    is_cached('command0/1.8s_0.wav')

    # Consistent dataset.
    consistent_loader = audio_dataloader.DataLoader.from_folder(
        spec, folder_path, shuffle=False)
    expected_labels = iter(
        ['background', 'background', 'command0', 'command1', 'command2'])
    expected_values = iter([0., 0., 5., 1., 3.])
    for feature, label_idx in consistent_loader.gen_dataset().unbatch():
      self.assertEqual(consistent_loader.index_to_label[label_idx],
                       next(expected_labels))
      self.assertEqual(feature.shape, (1, spec.expected_waveform_len))
      self.assertEqual(feature.dtype, tf.float32)
      # tf.audio.decode_wav op scales the int16 PCM to float value between -1
      # and 1 so the multiplier is 1 << 15
      # Check tensorflow/core/lib/wav/wav_io.cc for the implementation.
      self.assertNear(feature[0][0] * (1 << 15), next(expected_values), 1e-4)


if __name__ == '__main__':
  tf.test.main()
