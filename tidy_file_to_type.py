# 初始化一个空字典用于存储分类结果
import argparse
import functools

from ppacls.trainer import PPAClsTrainer
from ppacls.utils.utils import add_arguments, print_arguments

import os.path
import shutil
 

def tidy_file_to_type(src_path, dst_path):
    print(f"开始整理文件 {src_path} 到 {dst_path}")
    file_list = os.listdir(src_path)
    for file_name in file_list:
        parts = file_name.split("_")
        category = "_".join(parts[:-1])
        if os.path.isdir(f'{src_path}/{file_name}'):
            print(f"跳过文件夹 {file_name}")
            continue
        if not os.path.exists(f'{dst_path}/{category}'):
            os.makedirs(f'{dst_path}/{category}')
        if not os.path.exists(f'{dst_path}/{category}/{file_name}'):
            print(f"复制文件 {file_name} 到分类 {category} 下")
            shutil.copy(f'{src_path}/{file_name}', f'{dst_path}/{category}/{file_name}')
            


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_arg = functools.partial(add_arguments, argparser=parser)
    add_arg('model_name',          str,    'dog',        '模型名称')
    args = parser.parse_args()
    print_arguments(args=args)

    # mode_name = "dog"
    mode_name = args.model_name
    src_path = f"dataset/{mode_name}/src"
    dst_path = f"dataset/{mode_name}/audio"
    tidy_file_to_type(src_path, dst_path)

    