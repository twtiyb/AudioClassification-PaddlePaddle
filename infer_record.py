import wave
import librosa
import numpy as np
import pyaudio
import paddle

# 加载模型
model_path = 'models/inference'
model = paddle.jit.load(model_path)
model.eval()

# 录音参数
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
RECORD_SECONDS = 3
WAVE_OUTPUT_FILENAME = "infer_audio.wav"

# 打开录音
p = pyaudio.PyAudio()
stream = p.open(format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK)


# 读取音频数据
def load_data(data_path):
    # 读取音频
    wav, sr = librosa.load(data_path, sr=16000)
    spec_mag = librosa.feature.melspectrogram(y=wav, sr=sr, hop_length=256).astype(np.float32)
    mean = np.mean(spec_mag, 0, keepdims=True)
    std = np.std(spec_mag, 0, keepdims=True)
    spec_mag = (spec_mag - mean) / (std + 1e-5)
    spec_mag = spec_mag[np.newaxis, np.newaxis, :]
    spec_mag = spec_mag.astype('float32')
    return spec_mag


# 获取录音数据
def record_audio():
    print("开始录音......")

    frames = []
    for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
        data = stream.read(CHUNK)
        frames.append(data)

    print("录音已结束!")

    wf = wave.open(WAVE_OUTPUT_FILENAME, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(frames))
    wf.close()
    return WAVE_OUTPUT_FILENAME


# 预测
def infer(audio_path):
    data = load_data(audio_path)
    data = paddle.to_tensor(data, dtype='float32')
    # 执行预测
    output = model(data)
    result = paddle.nn.functional.softmax(output).numpy()
    print(result)
    # 显示图片并输出结果最大的label
    lab = np.argsort(result)[0][-1]
    return lab


if __name__ == '__main__':
    try:
        while True:
            try:
                # 加载数据
                audio_path = record_audio()
                # 获取预测结果
                label = infer(audio_path)
                print('预测的标签为：%d' % label)
            except:
                pass
    except Exception as e:
        print(e)
        stream.stop_stream()
        stream.close()
        p.terminate()
