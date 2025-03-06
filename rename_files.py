import os
import re

    

def rename_files(directory):
    # 确保目录存在
    if not os.path.exists(directory):
        print(f"目录 {directory} 不存在")
        return

    # 编译正则表达式模式
    pattern = re.compile(r'(.+)_(\d+)\.mp3$')

    # 遍历目录中的所有文件
    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            # 提取基础名称和数字
            base_name = match.group(1)
            number = match.group(2)
            
            # 构建新文件名：将 xxx_数字.mp3 改为 数字_xxx.mp3
            new_filename = f'{number}_{base_name}.mp3'
            
            # 构建完整路径
            old_path = os.path.join(directory, filename)
            new_path = os.path.join(directory, new_filename)
            
            try:
                os.rename(old_path, new_path)
                print(f'已重命名: {filename} -> {new_filename}')
            except OSError as e:
                print(f'重命名 {filename} 时发生错误: {e}')

if __name__ == '__main__':
    directory = 'dataset/dog/src'
    rename_files(directory) 