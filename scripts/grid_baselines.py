#!/bin/env python
import os
import subprocess
from os.path import join

from sklearn.model_selection import ParameterGrid

from onlikhorn.dataset import get_output_dir

SLURM_TEMPLATE = """#!/bin/env bash
#SBATCH --job-name=baselines
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1

#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=20G
#SBATCH --hint=nomultithread

#SBATCH --time=1:00:00
#SBATCH --output=run_%j.out
#SBATCH --error=run_%j.out

#SBATCH --partition=gpu_p2
#SBATCH --qos=qos_gpu-t4
#SBATCH -A glp@gpu


source load_modules

python {project_root}/baselines.py with {config_str} &

wait

exit 0

"""


def create_one(config, index=0):
    job_folder = join(get_output_dir(), 'baselines', 'jobs')
    project_root = os.path.abspath(os.getcwd())
    if not os.path.exists(job_folder):
        os.makedirs(job_folder)
    filename = join(job_folder, f'run_{index}.slurm')
    config_str = ' '.join(f'{key}={value}' for key, value in config.items())
    with open(filename, 'w+') as f:
        f.write(SLURM_TEMPLATE.format(project_root=project_root,
                                      filename=filename, config_str=config_str))
    return filename


grid = ParameterGrid({'data_source': ['dragon', 'gmm_1d', 'gmm_2d', 'gmm_10d'],
                      'n_samples': [10000],
                      'n_iter': [2000],
                      'seed': list(range(10)),
                      'epsilon': [1e-1, 1e-2, 1e-3]})
filenames = [create_one(config, index) for index, config in enumerate(grid)]

dry = False

if not dry:
    for filename in filenames:
        print("sbatch {}".format(filename))
        subprocess.check_output(
            "sbatch {}".format(filename), shell=True)
