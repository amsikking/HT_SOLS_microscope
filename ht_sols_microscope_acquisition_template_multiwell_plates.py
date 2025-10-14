import time
import os
import numpy as np
from datetime import datetime
from tifffile import imread, imwrite

import ht_sols_microscope as ht_sols

if __name__ == '__main__': # required block for sols_microscope
    # Create scope:
    scope = ht_sols.Microscope(max_allocated_bytes=100e9, ao_rate=1e4)

    # configure any hardware preferences:
    scope.XY_stage.set_velocity(120, 120)

    # Apply settings at least once: (required)
    scope.apply_settings(
        projection_mode=False,      # True/False
        projection_angle_deg=0,     # 0 -> 90 (0=coverslip, 35=native, 90=trad.)
        channels_per_slice=("LED",),# ('LED','405','488','561','640')
        power_per_channel=(10,),    # match channels 0-100% i.e. (5,0,20,30,100)
        emission_filter='Shutter',  # reset later, options are:
        # 'Shutter', 'Open'
        # 'ET445/58M', 'ET525/50M', 'ET600/50M', 'ET706/95M'
        # 'ZET405/488/561/640m', '(unused)', '(unused)', '(unused)'
        illumination_time_us=1*1e3, # reset later
        height_px=200,                          # 12 -> 800  (typical range)
        width_px=1500,                          # 60 -> 1500 (typical range)
        voxel_aspect_ratio=1,                   # 1  -> 10   (typical range)
        scan_range_um=250,                      # 10 -> 250  (typical range)
        volumes_per_buffer=1,                   # usually 1, can be more...
        autofocus_enabled=True,                # set 'True' for autofocus
        focus_piezo_z_um=(0,'relative'),        # = don't move
        XY_stage_position_mm=(0,0,'relative'),  # = don't move
        sample_ri=1.33,                         # 1.33 -> 1.51 (watery to oily)
        ls_focus_adjust_v=0,                    # -0.025 -> 0.025 (typical)
        ls_angular_dither_v=0,                  # 0 -> 1 (good for ill_us > 1ms)
        ).get_result()

    # adjust settings:
    scope.apply_settings(
        channels_per_slice=('405', '488', '561', '640'),
        power_per_channel=(40, 15, 30, 10),
        emission_filter='ZET405/488/561/640m',
        illumination_time_us=1*1e3,
        voxel_aspect_ratio=20,
        scan_range_um=250,
        ls_focus_adjust_v=0,
        ls_angular_dither_v=1,
        ).get_result()

    # Set tile overlap and spacing: 1 FOV?
    scale = 1 # 1 = tiles touch (no overlap), 0.9 = 10% overlap (stitching)
    tile_spacing_X_mm = scale * 1e-3 * scope.width_px * scope.sample_px_um
    tile_spacing_Y_mm = scale * 1e-3 * scope.scan_range_um
    # Revvity PhenoPlate 384-well: rows A to P, cols 1 to 24
    multiwell_plate_positions = ht_sols.get_multiwell_plate_positions(
        total_rows=16,
        total_cols=24,
        well_spacing_mm=4.5,
        start='A1',
        stop='B2',
        tile_rows=2,
        tile_cols=1,
        tile_spacing_X_mm=tile_spacing_X_mm,
        tile_spacing_Y_mm=tile_spacing_Y_mm,
        A1_ul_X_mm=50.2282,  
        A1_ul_Y_mm=-34.8627,
        A1_lr_X_mm=47.3277,
        A1_lr_Y_mm=-31.8921
        )

    # Make folder name for data:
    folder_label = 'ht_sols_acquisition_multiwell_plate'  # edit name
    dt = datetime.strftime(datetime.now(),'%Y-%m-%d_%H-%M-%S_000_')
    folder_name = dt + folder_label

    # Decide parameters for acquisition:
    time_points = 1     # how many time points for full acquisition?
    time_delay_s = None # delay between full acquisitions in seconds (or None)

    # Run acquisition: (tzcyx)
    for t in range(time_points):
        print('\nRunning time point %i:'%t)
        # start timer:
        t0 = time.perf_counter()
        acquire_tasks = 0
        for p in multiwell_plate_positions:
            print('-> time point %i (position:%s)'%(t, p[0]))
            # Move to XY position:
            scope.apply_settings(XY_stage_position_mm=p[1])
            # Aquire:
            filename = '%06i_%s.tif'%(t, p[0])
            scope.acquire(filename=filename,
                          folder_name=folder_name,
                          description='something...',
                          preview_only=False)
            acquire_tasks += 1
            if acquire_tasks > 10:                
                # Don't launch all tasks at once:
                scope.finish_all_tasks()
                print('(finished_all_tasks)')
                acquire_tasks = 0
        # finish timing:
        loop_time_s = time.perf_counter() - t0
        if t + 1 == time_points:
            break # avoid last delay            
        # Apply time delay if applicable:
        if time_delay_s is not None:
            if time_delay_s > loop_time_s:
                print('\nApplying time_delay_s: %0.2f'%time_delay_s)
                time.sleep(time_delay_s - loop_time_s)
            else:
                print('\n***WARNING***')
                print('time_delay_s not applied (loop_time_s > time_delay_s)')
    scope.close()
