# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Drums RNN generation code as a SequenceGenerator interface."""

from functools import partial

# internal imports
from magenta.models.drums_rnn import drums_rnn_model

import magenta.music as mm


class DrumsRnnSequenceGenerator(mm.BaseSequenceGenerator):
  """Shared Melody RNN generation code as a SequenceGenerator interface."""

  def __init__(self, model, details, steps_per_quarter=4, checkpoint=None,
               bundle=None):
    """Creates a DrumsRnnSequenceGenerator.

    Args:
      model: Instance of DrumsRnnModel.
      details: A generator_pb2.GeneratorDetails for this generator.
      steps_per_quarter: What precision to use when quantizing the melody. How
          many steps per quarter note.
      checkpoint: Where to search for the most recent model checkpoint. Mutually
          exclusive with `bundle`.
      bundle: A GeneratorBundle object that includes both the model checkpoint
          and metagraph. Mutually exclusive with `checkpoint`.
    """
    super(DrumsRnnSequenceGenerator, self).__init__(
        model, details, steps_per_quarter, checkpoint, bundle)

  def _generate(self, input_sequence, generator_options):
    if len(generator_options.input_sections) > 1:
      raise mm.SequenceGeneratorException(
          'This model supports at most one input_sections message, but got %s' %
          len(generator_options.input_sections))
    if len(generator_options.generate_sections) != 1:
      raise mm.SequenceGeneratorException(
          'This model supports only 1 generate_sections message, but got %s' %
          len(generator_options.generate_sections))

    qpm = (input_sequence.tempos[0].qpm
           if input_sequence and input_sequence.tempos
           else mm.DEFAULT_QUARTERS_PER_MINUTE)

    generate_section = generator_options.generate_sections[0]
    if generator_options.input_sections:
      input_section = generator_options.input_sections[0]
      primer_sequence = mm.extract_subsequence(
          input_sequence, input_section.start_time, input_section.end_time)
      input_start_step = self.seconds_to_steps(input_section.start_time, qpm)
    else:
      primer_sequence = input_sequence
      input_start_step = 0

    last_end_time = (max(n.end_time for n in primer_sequence.notes)
                     if primer_sequence.notes else 0)
    if last_end_time >= generate_section.start_time:
      raise mm.SequenceGeneratorException(
          'Got GenerateSection request for section that is before or equal to '
          'the end of the NoteSequence. This model can only extend sequences. '
          'Requested start time: %s, Final note end time: %s' %
          (generate_section.start_time, last_end_time))

    # Quantize the priming sequence.
    quantized_sequence = mm.quantize_note_sequence(
        primer_sequence, self.steps_per_quarter)
    # Setting gap_bars to infinite ensures that the entire input will be used.
    extracted_drum_tracks, _ = mm.extract_drum_tracks(
        quantized_sequence, search_start_step=input_start_step, min_bars=0,
        gap_bars=float('inf'))
    assert len(extracted_drum_tracks) <= 1

    start_step = self.seconds_to_steps(
        generate_section.start_time, qpm)
    end_step = self.seconds_to_steps(generate_section.end_time, qpm)

    if extracted_drum_tracks and extracted_drum_tracks[0]:
      drums = extracted_drum_tracks[0]
    else:
      # If no drum track could be extracted, create an empty drum track that
      # starts 1 step before the request start_step. This will result in 1 step
      # of silence when the drum track is extended below.
      drums = mm.DrumTrack([], start_step=max(0, start_step - 1))

    # Ensure that the drum track extends up to the step we want to start
    # generating.
    drums.set_length(start_step - drums.start_step)

    # Extract generation arguments from generator options.
    arg_types = {
        'temperature': lambda arg: arg.float_value,
        'beam_size': lambda arg: arg.int_value,
        'branch_factor': lambda arg: arg.int_value,
        'steps_per_iteration': lambda arg: arg.int_value
    }
    args = dict((name, value_fn(generator_options.args[name]))
                for name, value_fn in arg_types.items()
                if name in generator_options.args)

    generated_drums = self._model.generate_drum_track(
        end_step - drums.start_step, drums, **args)
    generated_sequence = generated_drums.to_sequence(qpm=qpm)
    assert (generated_sequence.total_time - generate_section.end_time) <= 1e-5
    return generated_sequence


def get_generator_map():
  """Returns a map from the generator ID to its SequenceGenerator class.

  Binds the `config` argument so that the constructor matches the
  BaseSequenceGenerator class.

  Returns:
    Map from the generator ID to its SequenceGenerator class with a bound
    `config` argument.
  """
  return {key: partial(DrumsRnnSequenceGenerator,
                       drums_rnn_model.DrumsRnnModel(config), config.details)
          for (key, config) in drums_rnn_model.default_configs.items()}
