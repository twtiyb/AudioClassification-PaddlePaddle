import argparse
import functools

from ppacls.trainer import PPAClsTrainer
from ppacls.utils.utils import add_arguments, print_arguments

parser = argparse.ArgumentParser(description=__doc__)
add_arg = functools.partial(add_arguments, argparser=parser)
add_arg('model_name',          str,    'dog',        '模型名称')
add_arg('config_name',          str,    'cam++.yml',        '配置文件名称')
add_arg('use_gpu',              bool,   False,        '是否使用GPU训练')
add_arg('max_duration',     int,    100,                        '提取特征的最大时长，避免过长显存不足，单位秒')
args = parser.parse_args()
print_arguments(args=args)

# 获取训练器
trainer = PPAClsTrainer(use_gpu=args.use_gpu, configs=f'dataset/{args.model_name}/configs/{args.config_name}')

# 提取特征保存文件
trainer.extract_features(save_dir=f'dataset/{args.model_name}/features', max_duration=args.max_duration)
