import argparse
import functools

from ppacls.predict import PPAClsPredictor
from ppacls.utils.record import RecordAudio
from ppacls.utils.utils import add_arguments, print_arguments

parser = argparse.ArgumentParser(description=__doc__)
add_arg = functools.partial(add_arguments, argparser=parser)
add_arg('configs',          str,    'configs/cam++.yml',   '配置文件')
add_arg('use_gpu',          bool,   True,                       '是否使用GPU预测')
add_arg('record_seconds',   int,    3,                          '录音长度')
add_arg('model_path',       str,    'models/CAMPPlus_Fbank/best_model/', '导出的预测模型文件路径')
args = parser.parse_args()
print_arguments(args=args)

# 获取识别器
predictor = PPAClsPredictor(configs=args.configs,
                            model_path=args.model_path,
                            use_gpu=args.use_gpu)

record_audio = RecordAudio()

if __name__ == '__main__':
    try:
        while True:
            # 加载数据
            input(f"按下回车键开机录音，录音{args.record_seconds}秒中：")
            audio_data = record_audio.record(record_seconds=args.record_seconds)
            # 获取预测结果
            label, s = predictor.predict(audio_data, sample_rate=record_audio.sample_rate)
            print(f'预测的标签为：{label}，得分：{s}')
    except Exception as e:
        print(e)
