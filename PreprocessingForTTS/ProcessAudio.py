import warnings

import librosa
import librosa.core as lb
import librosa.display as lbd
import matplotlib.pyplot as plt
import numpy
import numpy as np
import pyloudnorm as pyln
import soundfile as sf
import torch
from torchaudio.transforms import MuLawEncoding, MuLawDecoding, Resample
from torchaudio.transforms import Vad as VoiceActivityDetection

warnings.filterwarnings("ignore")


class AudioPreprocessor:
    def __init__(self, input_sr, output_sr=None, melspec_buckets=80, hop_length=256, n_fft=1024):
        """
        The parameters are by default set up to do well
        on a 16kHz signal. A different frequency may
        require different hop_length and n_fft (e.g.
        doubling frequency --> doubling hop_length and
        doubling n_fft)
        """
        self.sr = input_sr
        self.new_sr = output_sr
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.mel_buckets = melspec_buckets
        self.vad = VoiceActivityDetection(sample_rate=input_sr)
        self.mu_encode = MuLawEncoding()
        self.mu_decode = MuLawDecoding()
        self.meter = pyln.Meter(input_sr)
        self.final_sr = input_sr
        if output_sr is not None and output_sr != input_sr:
            self.resample = Resample(orig_freq=input_sr, new_freq=output_sr)
            self.final_sr = output_sr
        else:
            self.resample = lambda x: x

    def mel_spec_orig_sr(self, audio):
        return self.logmelfilterbank(audio=audio, sampling_rate=self.sr)

    def mel_spec_new_sr(self, audio):
        return self.logmelfilterbank(audio=audio, sampling_rate=self.new_sr)

    def apply_mu_law(self, audio):
        """
        brings the audio down from 16 bit
        resolution to 8 bit resolution to
        make using softmax to predict a
        wave from it more feasible.

        !CAREFUL! transforms the floats
        between -1 and 1 to integers
        between 0 and 255. So that is good
        to work with, but bad to save/listen
        to. Apply mu-law decoding before
        saving or listening to the audio.
        """
        return self.mu_encode(audio)

    def cut_silence_from_beginning(self, audio):
        """
        applies cepstral voice activity
        detection and noise reduction to
        cut silence from the beginning of
        a recording
        """
        return self.vad(torch.Tensor(audio))

    def to_mono(self, x):
        """
        make sure we deal with a 1D array
        """
        if len(x.shape) == 2:
            return lb.to_mono(numpy.transpose(x))
        else:
            return x

    def normalize_loudness(self, audio):
        """
        normalize the amplitudes according to
        their decibels, so this should turn any
        signal with different magnitudes into
        the same magnitude by analysing loudness
        """

        loudness = self.meter.integrated_loudness(audio)
        loud_normed = pyln.normalize.loudness(audio, loudness, -30.0)
        peak = numpy.amax(numpy.abs(loud_normed))
        peak_normed = numpy.divide(loud_normed, peak)
        return peak_normed

    def logmelfilterbank(self, audio, sampling_rate, fmin=40, fmax=8000, eps=1e-10):
        """
        Compute log-Mel filterbank
        """
        audio = audio.numpy()
        # get amplitude spectrogram
        x_stft = librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length,
                              win_length=None, window="hann", pad_mode="reflect")
        spc = np.abs(x_stft).T
        # get mel basis
        fmin = 0 if fmin is None else fmin
        fmax = sampling_rate / 2 if fmax is None else fmax
        mel_basis = librosa.filters.mel(sampling_rate, self.n_fft, self.mel_buckets, fmin, fmax)
        # apply log and return
        return torch.Tensor(np.log10(np.maximum(eps, np.dot(spc, mel_basis.T)))).transpose(0, 1)

    def normalize_audio(self, audio):
        """
        one function to apply them all in an
        order that makes sense.
        """
        audio = self.to_mono(audio)
        audio = self.normalize_loudness(audio)
        audio = self.cut_silence_from_beginning(audio)
        audio = self.resample(audio)
        return audio

    def visualize_cleaning(self, unclean_audio):
        """
        displays Mel Spectrogram of unclean audio
        and then displays Mel Spectrogram of the
        cleaned version.
        """
        fig, ax = plt.subplots(nrows=2, ncols=1)
        unclean_audio_mono = self.to_mono(unclean_audio)
        unclean_spec = self.audio_to_mel_spec_tensor(unclean_audio_mono, normalize=False).numpy()
        clean_spec = self.audio_to_mel_spec_tensor(unclean_audio_mono, normalize=True).numpy()
        lbd.specshow(unclean_spec, sr=self.sr, cmap='GnBu', y_axis='mel', ax=ax[0], x_axis='time')
        ax[0].set(title='Uncleaned Audio')
        ax[0].label_outer()
        if self.new_sr is not None:
            lbd.specshow(clean_spec, sr=self.new_sr, cmap='GnBu', y_axis='mel', ax=ax[1], x_axis='time')
        else:
            lbd.specshow(clean_spec, sr=self.sr, cmap='GnBu', y_axis='mel', ax=ax[1], x_axis='time')
        ax[1].set(title='Cleaned Audio')
        ax[1].label_outer()
        plt.show()

    def audio_to_wave_tensor(self, audio, normalize=True, mulaw=False):
        if mulaw:
            if normalize:
                return self.apply_mu_law(self.normalize_audio(audio))
            else:
                return self.apply_mu_law(torch.Tensor(audio))
        else:
            if normalize:
                return self.normalize_audio(audio)
            else:
                return torch.Tensor(audio)

    def audio_to_mel_spec_tensor(self, audio, normalize=True):
        if normalize:
            return self.mel_spec_new_sr(self.mu_decode(self.mu_encode(self.normalize_audio(audio))))
        else:
            return self.mel_spec_orig_sr(self.mu_decode(self.mu_encode(torch.Tensor(audio))))


if __name__ == '__main__':
    # load audio into numpy array
    wave, fs = sf.read("test_audio/test.wav")

    # create audio preprocessor object
    ap = AudioPreprocessor(input_sr=fs, output_sr=16000)

    # visualize a before and after of the cleaning
    ap.visualize_cleaning(wave)

    # write a cleaned version of the audio to listen to
    sf.write("test_audio/test_cleaned.wav", ap.normalize_audio(wave), ap.final_sr)

    # look at tensors of a wave representation and a mel spectrogram representation
    print("\n\nWave as Tensor (8 bit integer values, dtype=int64): \n{}".format(
        ap.audio_to_wave_tensor(wave, mulaw=True)))
    print("\n\nMelSpec as Tensor (16 bit float values, dtype=float32): \n{}".format(ap.audio_to_mel_spec_tensor(wave)))
