import argparse
import functools
import time

from ppacls.trainer import PPAClsTrainer
from ppacls.utils.utils import add_arguments, print_arguments

parser = argparse.ArgumentParser(description=__doc__)
add_arg = functools.partial(add_arguments, argparser=parser)
add_arg('model_name',          str,   'dog',         "模型名称")
add_arg('model_config',          str,   'cam++.yml',         "模型配置文件")
add_arg("use_gpu",          bool,  True,                        "是否使用GPU评估模型")
add_arg('save_matrix_path', str,   'output/images/',            "保存混合矩阵的路径")
add_arg('overwrites',       str,    None,    '覆盖配置文件中的参数，比如"train_conf.max_epoch=100"，多个用逗号隔开')
args = parser.parse_args()
print_arguments(args=args)

# 获取训练器
trainer = PPAClsTrainer(configs=f'dataset/{args.model_name}/configs/{args.model_config}', use_gpu=args.use_gpu, overwrites=args.overwrites)

# 开始评估
start = time.time()
loss, accuracy = trainer.evaluate(resume_model=f'dataset/{args.model_name}/models/CAMPPlus_Fbank/best_model',
                                  save_matrix_path=args.save_matrix_path)
end = time.time()
print('评估消耗时间：{}s，loss：{:.5f}，accuracy：{:.5f}'.format(int(end - start), loss, accuracy))
