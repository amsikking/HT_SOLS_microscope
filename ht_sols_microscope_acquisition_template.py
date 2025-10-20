import time

import ht_sols_microscope as ht_sols

if __name__ == '__main__': # required block for sols_microscope
    # Create scope:
    scope = ht_sols.Microscope(max_allocated_bytes=100e9, ao_rate=1e4)

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
        height_px=400,                          # 12 -> 800  (typical range)
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

    # Get current XY position for moving back at the end of the script:
    x_mm_0, y_mm_0 = scope.XY_stage_position_mm
    
    # Setup minimal positions for no moving (current FOV only):
    XY_stage_positions      = ((0, 0, 'relative'),)

    # Optional XY moves from position lists collected by GUI:
    # -> uncomment and copy paste lists to use...
    # ***CAUTION WHEN MOVING XY STAGE -> DOUBLE CHECK POSITIONS***
##    XY_stage_positions      = [ # copy past lists in here:
##        # e.g. here's 2 XY positions:
##        [-0.412, -4.9643],
##        [-0.528025, -4.9643],
##        ]
##    # convert to correct format for .apply_settings():
##    for xy in XY_stage_positions:
##        xy.append('absolute')

    # Make folder name for data:
    folder_name = ht_sols.prepend_datetime('ht_sols_acquisition_template')

    # Decide parameters for acquisition:
    time_points = 2     # how many time points for full acquisition?
    time_delay_s = None # delay between full acquisitions in seconds (or None)

    # Run acquisition: (tzcyx)
    current_time_point = 0
    for i in range(time_points):
        print('\nRunning time point %i:'%i)
        # start timer:
        t0 = time.perf_counter()
        for p in range(len(XY_stage_positions)):
            # Move to XY position:
            scope.apply_settings(XY_stage_position_mm=XY_stage_positions[p])
            print('-> position:%i'%p)
            # 488 example:
            filename488 = '%06i_%06i_488.tif'%(current_time_point, p)
            scope.apply_settings(
                channels_per_slice=('488',),
                power_per_channel=(5,),
                emission_filter='ET525/50M',
                illumination_time_us=1*1e3,
                voxel_aspect_ratio=2,
                scan_range_um=100,
                volumes_per_buffer=1,
                )
            scope.acquire(filename=filename488,
                          folder_name=folder_name,
                          description='488 something...',
                          preview_only=False)
            # 561 example:
            filename561 = '%06i_%06i_561.tif'%(current_time_point, p)
            scope.apply_settings(
                channels_per_slice=('561',),
                power_per_channel=(5,),
                emission_filter='ET600/50M',
                illumination_time_us=1*1e3,
                voxel_aspect_ratio=2,
                scan_range_um=100,
                volumes_per_buffer=1,
                )
            scope.acquire(filename=filename561,
                          folder_name=folder_name,
                          description='561 something...',
                          preview_only=False)
        # finish timing and increment time point if applicable:
        loop_time_s = time.perf_counter() - t0
        current_time_point += 1
        if current_time_point == time_points:
            break # avoid last delay
        # Apply time delay if applicable:
        if time_delay_s is not None:
            if time_delay_s > loop_time_s:
                print('\nApplying time_delay_s: %0.2f'%time_delay_s)
                time.sleep(time_delay_s - loop_time_s)
            else:
                print('\n***WARNING***')
                print('time_delay_s not applied (loop_time_s > time_delay_s)')

    # return to 'zero' starting position for user convenience
    scope.apply_settings(XY_stage_position_mm=(x_mm_0, y_mm_0, 'absolute'))
    scope.close()
