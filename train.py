import argparse
import functools

from ppacls.trainer import PPAClsTrainer
from ppacls.utils.utils import add_arguments, print_arguments

parser = argparse.ArgumentParser(description=__doc__)
add_arg = functools.partial(add_arguments, argparser=parser)
add_arg('configs',          str,    'configs/tdnn.yml',        '配置文件')
add_arg("use_gpu",          bool,   True,                       '是否使用GPU训练')
add_arg('save_model_path',  str,    'models/',                  '模型保存的路径')
add_arg('log_dir',          str,    'log/',                     '保存VisualDL日志文件的路径')
add_arg('resume_model',     str,    None,                       '恢复训练，当为None则不使用预训练模型')
add_arg('pretrained_model', str,    None,                       '预训练模型的路径，当为None则不使用预训练模型')
args = parser.parse_args()
print_arguments(args=args)

# 获取训练器
trainer = PPAClsTrainer(configs=args.configs, use_gpu=args.use_gpu)

trainer.train(save_model_path=args.save_model_path,
              log_dir=args.log_dir,
              resume_model=args.resume_model,
              pretrained_model=args.pretrained_model)
