import argparse
import functools

from ppacls.trainer import PPAClsTrainer
from ppacls.utils.utils import add_arguments, print_arguments

parser = argparse.ArgumentParser(description=__doc__)
add_arg = functools.partial(add_arguments, argparser=parser)
add_arg('model_name',              str,    'dog',        '模型名称')
add_arg('config_name',              str,    'cam++.yml',        '配置文件名称')
add_arg('data_augment_configs', str,    'augmentation.yml', '数据增强配置文件')
add_arg("use_gpu",              bool,   True,                       '是否使用GPU训练')
add_arg('save_model_path',      str,    'models/',                  '模型保存的路径')
add_arg('log_dir',              str,    'log/',                     '保存VisualDL日志文件的路径')
add_arg('resume_model',         str,    None,                       '恢复训练，当为None则不使用预训练模型')
add_arg('pretrained_model',     str,    None,                       '预训练模型的路径，当为None则不使用预训练模型')
add_arg('overwrites',           str,    None,    '覆盖配置文件中的参数，比如"train_conf.max_epoch=100"，多个用逗号隔开')
args = parser.parse_args()
print_arguments(args=args)

# 获取训练器
trainer = PPAClsTrainer(configs=f'dataset/{args.model_name}/configs/{args.config_name}',
                        use_gpu=args.use_gpu,
                        data_augment_configs=f'dataset/{args.model_name}/configs/{args.data_augment_configs}',
                        overwrites=args.overwrites)

trainer.train(save_model_path=f'dataset/{args.model_name}/models',
              log_dir=f'dataset/{args.model_name}/log',
              resume_model=args.resume_model,
              pretrained_model=args.pretrained_model)
