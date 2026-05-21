import numpy as np
from scipy import signal
from scipy.fft import fft

def time_domain_analysis(data: list[float]) -> dict:
    """时域分析"""
    arr = np.array(data)
    return {
        'mean': float(np.mean(arr)),
        'max': float(np.max(arr)),
        'min': float(np.min(arr)),
        'rms': float(np.sqrt(np.mean(arr**2))),
        'std': float(np.std(arr)),
    }

def frequency_domain_analysis(data: list[float], sampling_rate: float) -> dict:
    """频域分析 - FFT"""
    arr = np.array(data)
    n = len(arr)
    freqs = np.fft.fftfreq(n, 1/sampling_rate)
    fft_vals = np.fft.fft(arr)
    magnitudes = np.abs(fft_vals[:n//2])

    return {
        'frequencies': freqs[:n//2].tolist(),
        'magnitudes': magnitudes.tolist(),
        'dominant_freq': float(freqs[np.argmax(magnitudes)]),
    }

def apply_filter(data: list[float], filter_type: str, cutoff: float, sampling_rate: float) -> list[float]:
    """滤波处理"""
    nyquist = sampling_rate / 2
    if filter_type == 'lowpass':
        b, a = signal.butter(4, cutoff/nyquist, btype='low')
    elif filter_type == 'highpass':
        b, a = signal.butter(4, cutoff/nyquist, btype='high')
    elif filter_type == 'bandpass':
        b, a = signal.butter(4, [cutoff[0]/nyquist, cutoff[1]/nyquist], btype='band')
    else:
        return data

    return signal.filtfilt(b, a, data).tolist()
