"""
A simple script to convert all .tif files in the (raw) 'data' folder
to .zarr format
-> drop this into the acquisition folder with 'ht_sols_microscope.py' and
run to generate the 'data_zarr' folder
"""
# Imports from the python standard library:
import os
import shutil
import time

# Third party imports, installable via pip:
import numpy as np
from tifffile import imread
import zarr

# Our code, one .py file per module, copy files to your local directory:
from ht_sols_microscope import DataNative

# get critical metadata for creating the 'native' view:
metadata_folder = os.getcwd() + '\\metadata\\'
metadata_file_name0 = os.listdir(metadata_folder)[0]
with open(metadata_folder + metadata_file_name0, "r") as file:
    metadata_lines0 = file.readlines()
metadata_dict = {}
for line in metadata_lines0:
    split = line.split(':')
    key, value = split[0], split[1]
    metadata_dict[key] = value
voxel_aspect_ratio = float(metadata_dict['voxel_aspect_ratio'])
scan_step_size_px = int(metadata_dict['scan_step_size_px'])
print('voxel_aspect_ratio=%0.3f, scan_step_size_px=%i'%(
    voxel_aspect_ratio, scan_step_size_px))

# get data, check number of files and get shape:
data_folder = os.getcwd() + '\\data\\'
data_file_names = os.listdir(data_folder)
num_files = len(data_file_names)
data0 = imread(data_folder + data_file_names[0])
print('num_files=%i, shape=%s, dtype=%s'%(num_files, data0.shape, data0.dtype))

# create 'data_zarr' directory:
data_zarr_folder = os.getcwd() + '\\data_zarr\\'
print('Making directory: "%s"...'%data_zarr_folder, end='')
if os.path.exists(data_zarr_folder):
    input('\n***Delete folder: "%s"?*** (enter to continue)'%data_zarr_folder)
    shutil.rmtree(data_zarr_folder)
    os.makedirs(data_zarr_folder)
else:
    os.makedirs(data_zarr_folder)
print('done.')

# read in data files, get native view and convert to .zarr:
datanative = DataNative() # get processsing tool
t0 = time.perf_counter()
for file in data_file_names:
    print('reading data file: %s'%file)
    data = imread(data_folder + file)
    # convert data to 5D if needed:
    if len(data.shape) == 2: # 2D image
        data = data[np.newaxis, np.newaxis, np.newaxis, :, :]
    if len(data.shape) == 3: # 3D volume
        data = data[np.newaxis, :, np.newaxis, :, :]   
    if len(data.shape) == 4: # multi channel 3D volume
        data = data[np.newaxis, :]
    # otherwise data is 5D: 'tzcyx'
    print('getting data native...', end='')
    data_native = datanative.get(data, scan_step_size_px)
    print('done')
    # set 'chunk' size -> default is 1 image:
    chunks = [1] * 5 # standardise on 5D data, 'tzcyx'
    chunks[-1], chunks[-2] = data_native.shape[-1], data_native.shape[-2]
    chunks = tuple(chunks)
    print('chunks=%s'%str(chunks))
    # convert to zarr and store:
    print('creating .zarr...', end='')
    store = data_zarr_folder + file.split('.')[0] + '.zarr'
    arr = zarr.create(
        shape=data_native.shape,
        chunks=chunks,
        dtype='uint16',
        store=store)
    arr[:] = data_native
    print('done')

total_time_s = time.perf_counter() - t0
time_per_file_s = total_time_s / num_files

print('total_time_s=%s'%total_time_s)           # 8769.51s (2.43h) 737GB
print('time_per_file_s=%s'%time_per_file_s)     # 22.83s per 1.91GB file
