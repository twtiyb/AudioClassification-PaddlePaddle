import os


def create_dirs(model_name):
    os.makedirs(f'dataset/{model_name}/src')
    os.makedirs(f'dataset/{model_name}/audio')
    os.makedirs(f'dataset/{model_name}/models')
    os.makedirs(f'dataset/{model_name}/log')
    os.makedirs(f'dataset/{model_name}/configs')
    os.makedirs(f'dataset/{model_name}/label')  

if __name__ == '__main__':
    create_dirs('dog')