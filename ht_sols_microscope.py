# Imports from the python standard library:
import atexit
import os
import queue
import time
from datetime import datetime

# Third party imports, installable via pip:
import napari
import numpy as np
from scipy.ndimage import zoom, rotate, gaussian_filter1d
from tifffile import imread, imwrite

# Our code, one .py file per module, copy files to your local directory:
try:
    # https://github.com/amsikking/coherent_OBIS_LSLX_laser_box
    import coherent_OBIS_LSLX_laser_box
    import concurrency_tools as ct  # github.com/AndrewGYork/tools
    import ni_PCIe_6738             # github.com/amsikking/ni_PCIe_6738
    import pco_edge42_cl            # github.com/amsikking/pco_edge42_cl
    import pi_C_867_2U2             # github.com/amsikking/pi_C_867_2U2
    import pi_E_709_1C1L            # github.com/amsikking/pi_E_709_1C1L
    import prior_PureFocus850       # github.com/amsikking/prior_PureFocus850
    import sutter_Lambda_10_3       # github.com/amsikking/sutter_Lambda_10_3
    import thorlabs_MCM3000         # github.com/amsikking/thorlabs_MCM3000
    import thorlabs_MLJ_Z_stage     # github.com/amsikking/thorlabs_MLJ_Z_stage    
    # https://github.com/amsikking/any_immersion_remote_refocus_microscopy +
    # /blob/main/figures/zoom_lens/mechanics/zoom_lens.py
    import zoom_lens
    from napari_in_subprocess import display    # github.com/AndrewGYork/tools
except Exception as e:
    print('ht_sols_microscope.py -> One or more imports failed')
    print('ht_sols_microscope.py -> error =',e)

# HT SOLS optical configuration (edit as needed):
M1 = 200 / 5; Mscan = 100 / 100; M3 = 250 / 9;
camera_px_um = 6.5
tilt = np.deg2rad(55)
dichroic_mirror_options = {'ZT405/488/561/640rpc'   :0}
emission_filter_options = {'Shutter'                :0,
                           'Open'                   :1,
                           'ET445/58M'              :2,
                           'ET525/50M'              :3,
                           'ET600/50M'              :4,
                           'ET706/95M'              :5,
                           'ZET405/488/561/640m'    :6,
                           '(unused)'               :7,
                           '(unused)'               :8,
                           '(unused)'               :9}
objective1_options = {'name'  :('Nikon 40x0.95 air',
                                'Nikon 40x1.15 water',
                                'Nikon 40x1.30 oil'),
                      # absolute position on 'Z_drive' from alignment:
                      'BFP_um':(0, -137, -12023),
                      # min working distance spec:
                      'WD_um' :(170, 590, 240)}

class Microscope:
    def __init__(self,
                 max_allocated_bytes,   # Limit of available RAM for machine
                 ao_rate,               # slow ~1e3, medium ~1e4, fast ~1e5
                 name='HT-SOLS v1.1',
                 verbose=True,
                 print_warnings=True):
        self.max_allocated_bytes = max_allocated_bytes
        self.name = name
        self.verbose = verbose
        self.print_warnings = print_warnings
        if self.verbose: print("%s: opening..."%self.name)
        self.unfinished_tasks = queue.Queue()
        # init hardware/software:
        slow_camera_init = ct.ResultThread(
            target=self._init_camera).start()       #~3.6s
        slow_zoom_lens_init = ct.ResultThread(
            target=self._init_zoom_lens).start()    #~1.5s        
        slow_lasers_init = ct.ResultThread(
            target=self._init_lasers).start()       #~1.1s        
        slow_focus_init = ct.ResultThread(
            target=self._init_focus_piezo).start()  #~0.6s
        slow_XY_stage_init = ct.ResultThread(
            target=self._init_XY_stage).start()     #~0.4s
        slow_Z_stage_init = ct.ResultThread(
            target=self._init_Z_stage).start()      #~0.3s
        slow_fw_init = ct.ResultThread(
            target=self._init_filter_wheel).start() #~0.08s        
        slow_Z_drive_init = ct.ResultThread(
            target=self._init_Z_drive).start()      #~0.07s
        slow_autofocus_init = ct.ResultThread(
            target=self._init_autofocus).start()    #~0.015s
        self._init_display()                        #~1.3s
        self._init_datapreview()                    #~0.8s
        self._init_ao(ao_rate)                      #~0.2s
        slow_autofocus_init.get_result()
        slow_Z_drive_init.get_result()
        slow_fw_init.get_result()
        slow_Z_stage_init.get_result()
        slow_XY_stage_init.get_result()
        slow_focus_init.get_result()
        slow_lasers_init.get_result()
        slow_zoom_lens_init.get_result()
        slow_camera_init.get_result()
        # configure autofocus: (Z_drive, focus_piezo and autofocus initialized)
        self.autofocus.set_digipot_mode('Offset') # set for user convenience
        self.autofocus.set_piezo_range_um(769) # closest legal value to 800
        self.focus_piezo.set_analog_control_limits(
            v_min=0,    # 0-10V is 'self.focus_piezo.z_max'
            v_max=10,
            z_min_ai=0,
            z_max_ai=self.autofocus.piezo_range_um)
        self.autofocus_offset_lens = (
            self.autofocus._get_offset_lens_position())
        self.autofocus_sample_flag = self.autofocus.get_sample_flag()
        self.autofocus_focus_flag  = self.autofocus.get_focus_flag()
        # set defaults:
        # -> apply_settings args
        self.timestamp_mode = "binary+ASCII"
        self.camera._set_timestamp_mode(self.timestamp_mode) # default on
        self.autofocus_enabled = False
        self.focus_piezo_z_um = self.focus_piezo.z
        self.XY_stage_position_mm = self.XY_stage.x, self.XY_stage.y
        self.camera_preframes = 1 # ditch some noisy frames before recording?
        self.max_bytes_per_buffer = (2**31) # legal tiff
        self.max_data_buffers = 4 # camera, preview, display, filesave
        self.max_preview_buffers = self.max_data_buffers
        self.preview_line_px = 10 # line thickness for previews
        # The pco_edge42_cl has unreliable pixel rows at the top and bottom,
        # so for clean previews it's best to remove them:
        self.preview_crop_px = 3 # crop top and bottom pixel rows for previews
        # -> additional
        self.dichroic_mirror = tuple(dichroic_mirror_options.keys())[0]
        self.num_active_data_buffers = 0
        self.num_active_preview_buffers = 0
        self._settings_applied = False
        if self.verbose: print("\n%s: -> open and ready."%self.name)

    def _init_camera(self):
        if self.verbose: print("\n%s: opening camera..."%self.name)
        self.camera = ct.ObjectInSubprocess(
            pco_edge42_cl.Camera, verbose=False, close_method_name='close')
        if self.verbose: print("\n%s: -> camera open."%self.name)

    def _init_zoom_lens(self):
        if self.verbose: print("\n%s: opening zoom lens..."%self.name)
        self.zoom_lens = zoom_lens.ZoomLens(
            stage1_port='COM13',
            stage2_port='COM14',
            stage3_port='COM15',
            verbose=False,
            fast_init=False)
        if self.verbose: print("\n%s: -> zoom lens open."%self.name)
        atexit.register(self.zoom_lens.close)

    def _init_lasers(self):
        if self.verbose: print("\n%s: opening lasers..."%self.name)
        self.lasers = coherent_OBIS_LSLX_laser_box.Controller(
            which_port='COM16', control_mode='analog', verbose=False)
        for laser in self.lasers.lasers:
            self.lasers.set_enable('ON', laser)
        if self.verbose: print("\n%s: -> lasers open."%self.name)

    def _init_focus_piezo(self):
        if self.verbose: print("\n%s: opening focus piezo..."%self.name)
        self.focus_piezo = pi_E_709_1C1L.Controller(
            which_port='COM5', z_min_um=0, z_max_um=800, verbose=False)
        if self.verbose: print("\n%s: -> focus piezo open."%self.name)
        atexit.register(self.focus_piezo.close)

    def _init_XY_stage(self):
        if self.verbose: print("\n%s: opening XY stage..."%self.name)        
        self.XY_stage = pi_C_867_2U2.Controller(
            which_port='COM4', verbose=False)
        if self.verbose: print("\n%s: -> XY stage open."%self.name)
        atexit.register(self.XY_stage.close)

    def _init_Z_stage(self):
        if self.verbose: print("\n%s: opening Z stage..."%self.name)
        self.Z_stage = thorlabs_MLJ_Z_stage.ZStage(
            which_ports=('COM11','COM12'),
            limits_mm=(0, 30),
            velocity_mmps=0.2,
            verbose=False)
        if self.verbose: print("\n%s: -> Z stage open."%self.name)
        atexit.register(self.Z_stage.close)

    def _init_filter_wheel(self):
        if self.verbose: print("\n%s: opening filter wheel..."%self.name)
        self.filter_wheel = sutter_Lambda_10_3.Controller(
            which_port='COM7', verbose=False)
        if self.verbose: print("\n%s: -> filter wheel open."%self.name)
        atexit.register(self.filter_wheel.close)

    def _init_Z_drive(self):
        if self.verbose: print("\n%s: opening Z drive..."%self.name)
        self.Z_drive = thorlabs_MCM3000.Controller(
            which_port='COM9',
            stages=(None, None, 'ZFM2020'),
            reverse=(False, False, False),
            verbose=False)
        self.Z_drive_position_um = round(self.Z_drive.position_um[2]) # ch = 2
        # check z position is legal and assign objective1:
        self.objective1 = objective1_options['BFP_um'].index(
            self.Z_drive_position_um)
        self.objective1_name  = objective1_options['name'][self.objective1]
        self.objective1_WD_um = objective1_options['WD_um'][self.objective1]        
        if self.verbose:
            print("\n%s: -> objective1 = %s"%(self.name, self.objective1_name))
            print("\n%s: -> Z drive open."%self.name)
        atexit.register(self.Z_drive.close)

    def _init_autofocus(self):
        if self.verbose: print("\n%s: opening autofocus..."%self.name)
        self.autofocus = prior_PureFocus850.Controller(
            which_port='COM6', verbose=False)
        if self.verbose: print("\n%s: -> autofocus open."%self.name)
        atexit.register(self.autofocus.close)

    def _init_display(self):
        if self.verbose: print("\n%s: opening display..."%self.name)  
        self.display = display(display_type=_CustomNapariDisplay)
        if self.verbose: print("\n%s: -> display open."%self.name) 

    def _init_datapreview(self):
        if self.verbose: print("\n%s: opening datapreview..."%self.name) 
        self.datapreview = ct.ObjectInSubprocess(DataPreview)
        if self.verbose: print("\n%s: -> datapreview open."%self.name)        

    def _init_ao(self, ao_rate):
        self.illumination_sources = ( # controlled by ao
            'LED', '405', '488', '561', '640', '405_on_during_rolling')
        self.names_to_voltage_channels = {
            '405_TTL'           : 0,
            '405_power'         : 1,
##            '445_TTL'           : 2,
##            '445_power'         : 3,
            '488_TTL'           : 4,
            '488_power'         : 5,
            '561_TTL'           : 6,
            '561_power'         : 7,
            '640_TTL'           : 8,
            '640_power'         : 9,
            'LED_power'         : 10,
            'camera'            : 11,
            'galvo'             : 12,
##            'snoutfocus_piezo'  : 13,
##            'snoutfocus_shutter': 14,
            'LSx_BFP'           : 16,
            'LSy_BFP'           : 17,
            'LSx_IMG'           : 18,
            'LSy_IMG'           : 19,
            'shear'             : 20,
            }
        if self.verbose: print("\n%s: opening ao card..."%self.name)
        self.ao = ni_PCIe_6738.DAQ(
            num_channels=21, rate=ao_rate, verbose=False)
        if self.verbose: print("\n%s: -> ao card open."%self.name)
        atexit.register(self.ao.close)

    def _check_memory(self):        
        # Data:
        slices = self.slices_per_volume
        h_px = self.height_px
        if self.projection_mode:
            slices = 1
            h_px = self.projection_height_px
        self.images = (
            self.volumes_per_buffer * len(self.channels_per_slice) * slices)
        self.bytes_per_data_buffer = 2 * self.images * h_px * self.width_px
        self.data_buffer_exceeded = False
        if self.bytes_per_data_buffer > self.max_bytes_per_buffer:
            self.data_buffer_exceeded = True
            if self.print_warnings:
                print("\n%s: ***WARNING***: settings rejected"%self.name)
                print("%s: -> data_buffer_exceeded"%self.name)
                print("%s: -> reduce settings"%self.name +
                      " or increase 'max_bytes_per_buffer'")
        # Preview:
        self.preview_shape = DataPreview.shape(
            self.projection_mode,
            self.projection_angle_deg,
            self.volumes_per_buffer,
            self.slices_per_volume,
            len(self.channels_per_slice),
            h_px,
            self.width_px,
            self.sample_px_um,
            self.scan_step_size_px,
            self.preview_line_px,
            self.preview_crop_px,
            self.timestamp_mode)
        self.bytes_per_preview_buffer = 2 * int(np.prod(self.preview_shape))
        self.preview_buffer_exceeded = False
        if self.bytes_per_preview_buffer > self.max_bytes_per_buffer:
            self.preview_buffer_exceeded = True
            if self.print_warnings:
                print("\n%s: ***WARNING***: settings rejected"%self.name)
                print("%s: -> preview_buffer_exceeded"%self.name)
                print("%s: -> reduce settings"%self.name +
                      " or increase 'max_bytes_per_buffer'")
        # Total:
        self.total_bytes = (
            self.bytes_per_data_buffer * self.max_data_buffers +
            self.bytes_per_preview_buffer * self.max_preview_buffers)
        self.total_bytes_exceeded = False
        if self.total_bytes > self.max_allocated_bytes:
            self.total_bytes_exceeded = True
            if self.print_warnings:
                print("\n%s: ***WARNING***: settings rejected"%self.name)
                print("%s: -> total_bytes_exceeded"%self.name)
                print("%s: -> reduce settings"%self.name +
                      " or increase 'max_allocated_bytes'")
        return None

    def _calculate_voltages(self):
        n2c = self.names_to_voltage_channels # nickname
        # Timing information:
        exposure_px = self.ao.s2p(1e-6 * self.camera.exposure_us)
        rolling_px =  self.ao.s2p(1e-6 * self.camera.rolling_time_us)
        jitter_px = max(self.ao.s2p(30e-6), 1)
        period_px = max(exposure_px, rolling_px) + jitter_px
        # Galvo voltages:
        galvo_volts_per_um = 0.011395 # calibrated using laser spot
        galvo_scan_volts = galvo_volts_per_um * self.scan_range_um
        galvo_voltages = np.linspace(
            - galvo_scan_volts/2, galvo_scan_volts/2, self.slices_per_volume)
        # Shear galvo voltages:
        galvo_volts_per_px = 0.0021097 # calibrated using AMS-AGY edge
        galvo_shear_volts = galvo_volts_per_px * self.galvo_shear_px
        # Calculate voltages:
        voltages = []
        # Add preframes (if any):
        for frames in range(self.camera_preframes):
            v = np.zeros((period_px, self.ao.num_channels), 'float64')
            v[:rolling_px, n2c['camera']] = 5 # falling edge-> light on!
            voltages.append(v)
        for volumes in range(self.volumes_per_buffer):
            # TODO: either bidirectional volumes, or smoother galvo flyback
            slices = self.slices_per_volume
            if self.projection_mode: slices = 1
            for _slice in range(slices):
                for channel, power in zip(self.channels_per_slice,
                                          self.power_per_channel):
                    v = np.zeros((period_px, self.ao.num_channels), 'float64')
                    v[:rolling_px, n2c['camera']] = 5 # falling edge-> light on!
                    light_on_px = rolling_px
                    if channel in ('405_on_during_rolling',): light_on_px = 0
                    if channel != 'LED': # i.e. laser channels
                        v[light_on_px:period_px - jitter_px,
                          n2c[channel + '_TTL']] = 10 # /4 = 2.5V buffer output 
                    v[light_on_px:period_px - jitter_px,
                      n2c[channel + '_power']] = 4.5 * power / 100
                    # light sheet focus adjust:
                    v[:, n2c['LSx_BFP']] = self.ls_focus_adjust_v
                    ramp_px = period_px - jitter_px - light_on_px
                    if self.projection_mode:
                        gs_v = galvo_scan_volts / 2
                        v[light_on_px:period_px - jitter_px,
                          n2c['galvo']] = np.linspace(-gs_v, gs_v, ramp_px)
                        # shear galvos:
                        sh_v = galvo_shear_volts / 2
                        v[light_on_px:period_px - jitter_px,
                          n2c['shear']] = np.linspace(-sh_v, sh_v, ramp_px)
                    else:
                        v[:, n2c['galvo']] = galvo_voltages[_slice]
                        # light sheet angular dither:
                        ad_v = self.ls_angular_dither_v
                        v[light_on_px:period_px - jitter_px,
                          n2c['LSx_IMG']] = np.linspace(-ad_v, ad_v, ramp_px)
                    voltages.append(v)
        voltages = np.concatenate(voltages, axis=0)
        # Timing attributes:
        self.buffer_time_s = self.ao.p2s(voltages.shape[0])
        self.volumes_per_s = self.volumes_per_buffer / self.buffer_time_s
        return voltages

    def _plot_voltages(self):
        import matplotlib.pyplot as plt
        # Reverse lookup table; channel numbers to names:
        c2n = {v:k for k, v in self.names_to_voltage_channels.items()}
        for c in range(self.voltages.shape[1]):
            plt.plot(self.voltages[:, c], label=c2n.get(c, f'ao-{c}'))
        plt.legend(loc='upper right')
        xlocs, xlabels = plt.xticks()
        plt.xticks(xlocs, [self.ao.p2s(l) for l in xlocs])
        plt.ylabel('Volts')
        plt.xlabel('Seconds')
        plt.show()

    def _prepare_to_save(
        self, filename, folder_name, description, display, preview_only):
        def make_folders(folder_name):
            os.makedirs(folder_name)
            os.makedirs(folder_name + '\\data')
            os.makedirs(folder_name + '\\metadata')
            os.makedirs(folder_name + '\\preview')                    
        assert type(filename) is str
        if folder_name is None:
            dt, i, l = prepend_datetime(), 0, 'ht_sols'
            folder_name = dt + '%03i_'%i + l
            while os.path.exists(folder_name): # check overwriting
                i +=1
                folder_name = dt + '%03i_'%i + l
            make_folders(folder_name)
        else:
            if not os.path.exists(folder_name): make_folders(folder_name)
        data_path =     folder_name + '\\data\\'     + filename
        metadata_path = folder_name + '\\metadata\\' + filename
        preview_path =  folder_name + '\\preview\\'  + filename
        # save metadata:
        to_save = {
            # date and time:
            'Date':datetime.strftime(datetime.now(),'%Y-%m-%d'),
            'Time':datetime.strftime(datetime.now(),'%H:%M:%S'),
            # args from 'acquire':
            'filename':filename,
            'folder_name':folder_name,
            'description':description,
            'display':display,
            'preview_only':preview_only,
            # attributes from 'apply_settings':
            # -> args
            'projection_mode':self.projection_mode,
            'projection_angle_deg':self.projection_angle_deg,
            'channels_per_slice':tuple(self.channels_per_slice),
            'power_per_channel':tuple(self.power_per_channel),
            'emission_filter':self.emission_filter,
            'illumination_time_us':self.illumination_time_us,
            'height_px':self.height_px,
            'width_px':self.width_px,
            'timestamp_mode':self.timestamp_mode,
            'voxel_aspect_ratio':self.voxel_aspect_ratio,
            'scan_range_um': self.scan_range_um,
            'volumes_per_buffer':self.volumes_per_buffer,
            'autofocus_enabled':self.autofocus_enabled,
            'focus_piezo_z_um':self.focus_piezo_z_um,
            'XY_stage_position_mm':self.XY_stage_position_mm,
            'sample_ri':self.sample_ri,
            'ls_focus_adjust_v':self.ls_focus_adjust_v,
            'ls_angular_dither_v':self.ls_angular_dither_v,
            'camera_preframes':self.camera_preframes,
            'max_bytes_per_buffer':self.max_bytes_per_buffer,
            'max_data_buffers':self.max_data_buffers,
            'max_preview_buffers':self.max_preview_buffers,
            'preview_line_px':self.preview_line_px,
            'preview_crop_px':self.preview_crop_px,
            # -> calculated
            'scan_step_size_px':self.scan_step_size_px,
            'slices_per_volume':self.slices_per_volume,
            'scan_step_size_um':self.scan_step_size_um,
            'galvo_shear_px':self.galvo_shear_px,
            'projection_height_px':self.projection_height_px,
            'buffer_time_s':self.buffer_time_s,
            'volumes_per_s':self.volumes_per_s,
            # -> additional
            'autofocus_offset_lens':self.autofocus_offset_lens,
            'autofocus_sample_flag':self.autofocus_sample_flag,
            'autofocus_focus_flag':self.autofocus_focus_flag,
            'Z_stage_position_mm':self.Z_stage.stage1.position_mm,
            'Z_drive_position_um':self.Z_drive_position_um,
            'zoom_lens_f_mm':self.zoom_lens_f_mm,
            'objective1_name':self.objective1_name,
            'objective1_WD_um':self.objective1_WD_um,
            # optical configuration:
            'M1':M1,
            'Mscan':Mscan,
            'M2':self.M2,
            'M3':M3,
            'MRR':self.MRR,
            'Mtot':self.Mtot,
            'camera_px_um':camera_px_um,
            'sample_px_um':self.sample_px_um,
            'tilt':tilt,
            'tilt_deg':np.rad2deg(tilt),
            'dichroic_mirror':self.dichroic_mirror,
            }
        with open(os.path.splitext(metadata_path)[0] + '.txt', 'w') as file:
            for k, v in to_save.items():
                file.write(k + ': ' + str(v) + '\n')
        return data_path, preview_path

    def _get_data_buffer(self, shape, dtype):
        while self.num_active_data_buffers >= self.max_data_buffers:
            time.sleep(1e-3) # 1.7ms min
        # Note: this does not actually allocate the memory. Allocation happens
        # during the first 'write' process inside camera.record_to_memory
        data_buffer = ct.SharedNDArray(shape, dtype)
        self.num_active_data_buffers += 1
        return data_buffer

    def _release_data_buffer(self, shared_numpy_array):
        assert isinstance(shared_numpy_array, ct.SharedNDArray)
        self.num_active_data_buffers -= 1

    def _get_preview_buffer(self, shape, dtype):
        while self.num_active_preview_buffers >= self.max_preview_buffers:
            time.sleep(1e-3) # 1.7ms min
        # Note: this does not actually allocate the memory. Allocation happens
        # during the first 'write' process inside camera.record_to_memory
        preview_buffer = ct.SharedNDArray(shape, dtype)
        self.num_active_preview_buffers += 1
        return preview_buffer

    def _release_preview_buffer(self, shared_numpy_array):
        assert isinstance(shared_numpy_array, ct.SharedNDArray)
        self.num_active_preview_buffers -= 1

    def apply_settings( # Must call before .acquire()
        self,
        projection_mode=None,       # Bool
        projection_angle_deg=None,  # Float
        channels_per_slice=None,    # Tuple of strings
        power_per_channel=None,     # Tuple of floats
        emission_filter=None,       # String
        illumination_time_us=None,  # Float
        height_px=None,             # Int
        width_px=None,              # Int
        timestamp_mode=None,        # "off" or "binary" or "binary+ASCII"
        voxel_aspect_ratio=None,    # Int
        scan_range_um=None,         # Int or float
        volumes_per_buffer=None,    # Int
        autofocus_enabled=None,     # Bool
        focus_piezo_z_um=None,      # (Float, "relative" or "absolute")
        XY_stage_position_mm=None,  # (Float, Float, "relative" or "absolute")
        sample_ri=None,             # Float
        ls_focus_adjust_v=None,     # Float
        ls_angular_dither_v=None,   # Float
        camera_preframes=None,      # Int
        max_bytes_per_buffer=None,  # Int
        max_data_buffers=None,      # Int
        max_preview_buffers=None,   # Int
        preview_line_px=None,       # Int
        preview_crop_px=None,       # Int
        ):
        args = locals()
        args.pop('self')
        def settings_task(custody):
            custody.switch_from(None, to=self.camera) # Safe to change settings
            self._settings_applied = False # In case the thread crashes
            # Attributes must be set previously or currently:
            for k, v in args.items(): 
                if v is not None:
                    setattr(self, k, v) # A lot like self.x = x
                assert hasattr(self, k), (
                    "%s: attribute %s must be set at least once"%(self.name, k))
            if (projection_mode is not None or
                projection_angle_deg is not None or
                height_px is not None or
                width_px is not None or
                voxel_aspect_ratio is not None or
                scan_range_um is not None or
                sample_ri is not None):
                # update sample_ri dependents
                assert 1.33 <= self.sample_ri <= 1.51, 'sample_ri out of range'
                self.zoom_lens_f_mm = round(200 / self.sample_ri, 1)
                # -> enough precision and sets f_mm=132.5 for ri=1.51 (legal)
                if self.zoom_lens_f_mm > 150:
                    self.zoom_lens_f_mm = 150 # legalize ri 1.33 to exactly 4/3
                self.M2 = 5 / self.zoom_lens_f_mm
                self.MRR = M1 * Mscan * self.M2; self.Mtot = self.MRR * M3;
                self.sample_px_um = camera_px_um / self.Mtot
                # update voxel_aspect_ratio/scan_range_um dependents
                assert isinstance(self.projection_mode, bool)
                assert 0 <= self.projection_angle_deg <= 90, (
                        'projection_angle_deg out of range')
                if self.projection_mode: # set var = 0 -> scan_step_size_px = 1
                    self.voxel_aspect_ratio = 0
                self.scan_step_size_px, self.slices_per_volume = (
                    calculate_cuboid_voxel_scan(
                        self.sample_px_um,
                        self.voxel_aspect_ratio,
                        self.scan_range_um))
                self.scan_step_size_um = calculate_scan_step_size_um(
                    self.sample_px_um, self.scan_step_size_px)
                self.voxel_aspect_ratio = calculate_voxel_aspect_ratio(
                    self.scan_step_size_px)
                self.scan_range_um = calculate_scan_range_um(
                    self.sample_px_um,
                    self.scan_step_size_px,
                    self.slices_per_volume)
                assert 0 <= self.scan_range_um <= 500 # optical limit
                # calculate projection pixels along light-sheet/chip:
                # 'law of sines':
                total_scan_px = self.scan_range_um / self.sample_px_um
                phi = np.deg2rad(self.projection_angle_deg)
                gam = np.pi - phi - tilt
                self.galvo_shear_px = int(
                    round(total_scan_px * np.sin(phi) / np.sin(gam)))
                # work out and legalize the correct h_px, w_px and roi:
                h_px, w_px = height_px, width_px # shorthand
                if height_px is None: h_px = self.height_px
                if width_px is None:  w_px = self.width_px
                self.height_px, self.width_px, self.roi_px = (
                    pco_edge42_cl.legalize_image_size(
                        h_px, w_px, verbose=False))
                self.projection_height_px = 0
                if self.projection_mode: # apply height_px needed on camera
                    h_px = self.height_px + self.galvo_shear_px
                    if h_px > 2048: h_px = 2048 # limit to legal
                    self.projection_height_px, self.width_px, self.roi_px = (
                        pco_edge42_cl.legalize_image_size(
                            h_px, w_px, verbose=False))
            self._check_memory()
            if (self.data_buffer_exceeded or
                self.preview_buffer_exceeded or
                self.total_bytes_exceeded):
                custody.switch_from(self.camera, to=None)
                return None
            # Send hardware commands, slowest to fastest:
            if XY_stage_position_mm is not None:
                assert XY_stage_position_mm[2] in ('relative', 'absolute')
                x, y = XY_stage_position_mm[0], XY_stage_position_mm[1]
                if XY_stage_position_mm[2] == 'relative':
                    self.XY_stage.move_mm(x, y, block=False)
                if XY_stage_position_mm[2] == 'absolute':
                    self.XY_stage.move_mm(x, y, relative=False, block=False)
            else: # must update XY stage attributes if joystick was used
                update_XY_stage_position_thread = ct.ResultThread(
                    target=self.XY_stage.get_position_mm).start()
            if sample_ri is not None:
                set_zoom_lens_f_mm_thread = ct.ResultThread(
                    target=self.zoom_lens.set_focal_length_mm,
                    args=(self.zoom_lens_f_mm,)).start()
            if emission_filter is not None:
                self.filter_wheel.move(
                    emission_filter_options[emission_filter], block=False)
            if autofocus_enabled is not None:
                assert isinstance(autofocus_enabled, bool)
                if autofocus_enabled:
                    sample_flags = []
                    for flags in range(3):
                        sample_flags.append(self.autofocus.get_sample_flag())
                    if any(sample_flags): # sample detected?
                        self.autofocus.set_servo_enable(False)
                        self.autofocus.set_piezo_voltage( # ~zero motion volts
                            self.focus_piezo.get_voltage_for_move_um(0))
                        self.focus_piezo.set_analog_control_enable(True)
                        self.autofocus.set_servo_enable(True)
                    else: # no sample detected, don't enable autofocus servo
                        self.autofocus_enabled = False
                        if self.print_warnings:
                            print("\n%s: ***WARNING***: "%self.name +
                                  "autofocus_sample_flag=FALSE")
                            print("\n%s: ***WARNING***: "%self.name +
                                  "autofocus_enabled=FALSE")
                else:
                    self.focus_piezo.set_analog_control_enable(False)
                    self.focus_piezo_z_um = self.focus_piezo.z # update attr
                    self.autofocus.set_servo_enable(False)                
            if focus_piezo_z_um is not None:
                if not self.autofocus_enabled:
                    assert focus_piezo_z_um[1] in ('relative', 'absolute')
                    z = focus_piezo_z_um[0]
                    if focus_piezo_z_um[1] == 'relative':
                        self.focus_piezo.move_um(z, block=False)
                    if focus_piezo_z_um[1] == 'absolute':
                        self.focus_piezo.move_um(z, relative=False, block=False)
                else:
                    if focus_piezo_z_um != (0,'relative'):
                        raise Exception(
                            'cannot move focus piezo with autofocus enabled')
            if (projection_mode is not None or
                projection_angle_deg is not None or
                height_px is not None or
                width_px is not None or
                illumination_time_us is not None or
                scan_range_um is not None or
                sample_ri is not None):
                self.camera._disarm()
                self.camera._set_roi(self.roi_px) # height_px updated first
                self.camera._set_exposure_time_us(int(
                    self.illumination_time_us + self.camera.rolling_time_us))
                self.camera._arm(self.camera._num_buffers)
            if timestamp_mode is not None:
                self.camera._set_timestamp_mode(timestamp_mode)
            check_write_voltages_thread = False
            if (projection_mode is not None or
                projection_angle_deg is not None or
                channels_per_slice is not None or
                power_per_channel is not None or
                height_px is not None or
                illumination_time_us is not None or
                voxel_aspect_ratio is not None or
                scan_range_um is not None or
                volumes_per_buffer is not None or
                sample_ri is not None or
                ls_focus_adjust_v is not None or
                ls_angular_dither_v is not None or
                camera_preframes is not None):
                for channel in self.channels_per_slice:
                    assert channel in self.illumination_sources
                assert len(self.power_per_channel) == (
                    len(self.channels_per_slice))
                for power in self.power_per_channel: assert 0 <= power <= 100
                assert type(self.volumes_per_buffer) is int
                assert self.volumes_per_buffer > 0
                assert -0.1 <= self.ls_focus_adjust_v <= 0.1 # sensible limit
                assert 0 <= self.ls_angular_dither_v <= 1 # optical limit
                assert type(self.camera_preframes) is int
                self.camera.num_images = ( # update attribute
                    self.images + self.camera_preframes)
                self.voltages = self._calculate_voltages()
                write_voltages_thread = ct.ResultThread(
                    target=self.ao._write_voltages,
                    args=(self.voltages,)).start()
                check_write_voltages_thread = True
            # Finalize hardware commands, fastest to slowest:
            if focus_piezo_z_um is not None:
                self.focus_piezo._finish_moving()
                self.focus_piezo_z_um = self.focus_piezo.z
            if emission_filter is not None:
                self.filter_wheel._finish_moving()
            if sample_ri is not None:
                set_zoom_lens_f_mm_thread.get_result()
            if XY_stage_position_mm is not None:
                self.XY_stage._finish_moving()
                self.XY_stage_position_mm = self.XY_stage.x, self.XY_stage.y
            else:
                update_XY_stage_position_thread.get_result()
                self.XY_stage_position_mm = self.XY_stage.x, self.XY_stage.y
            if check_write_voltages_thread:
                write_voltages_thread.get_result()
            self._settings_applied = True
            custody.switch_from(self.camera, to=None) # Release camera
        settings_thread = ct.CustodyThread(
            target=settings_task, first_resource=self.camera).start()
        self.unfinished_tasks.put(settings_thread)
        return settings_thread

    def acquire(self,               # 'tzcyx' format
                filename=None,      # None = no save, same string = overwrite
                folder_name=None,   # None = new folder, same string = re-use
                description=None,   # Optional metadata description
                display=True,       # Optional turn off
                preview_only=False):# Save preview only, raw data discarded
        def acquire_task(custody):
            custody.switch_from(None, to=self.camera) # get camera
            if not self._settings_applied:
                if self.print_warnings:
                    print("\n%s: ***WARNING***: settings not applied"%self.name)
                    print("%s: -> please apply legal settings"%self.name)
                    print("%s: (all arguments must be specified at least once)")
                custody.switch_from(self.camera, to=None)
                return
            if self.autofocus_enabled: # update attributes:
                self.focus_piezo_z_um = self.focus_piezo.get_position(
                    verbose=False)
                self.autofocus_offset_lens = (
                    self.autofocus._get_offset_lens_position())
                self.autofocus_sample_flag = self.autofocus.get_sample_flag()
                self.autofocus_focus_flag  = self.autofocus.get_focus_flag()
                if self.print_warnings:
                    if not self.autofocus_sample_flag:
                        print("\n%s: ***WARNING***: "%self.name +
                              "self.autofocus_sample_flag=FALSE")
                    if not self.autofocus_focus_flag:
                        print("\n%s: ***WARNING***: "%self.name +
                              "autofocus_focus_flag=FALSE")
            # must update XY stage position attributes in case joystick was used
            # no thread (blocking) so metatdata in _prepare_to_save is current
            self.XY_stage_position_mm = self.XY_stage.get_position_mm()
            if filename is not None:
                prepare_to_save_thread = ct.ResultThread(
                    target=self._prepare_to_save,
                    args=(filename,
                          folder_name,
                          description,
                          display,
                          preview_only)).start()
            # We have custody of the camera so attribute access is safe:
            pm   = self.projection_mode
            pa   = self.projection_angle_deg
            vo   = self.volumes_per_buffer
            sl   = self.slices_per_volume
            ch   = len(self.channels_per_slice)
            h_px = self.height_px
            w_px = self.width_px
            s_um = self.sample_px_um
            s_px = self.scan_step_size_px
            l_px = self.preview_line_px
            c_px = self.preview_crop_px
            ts   = self.timestamp_mode
            im   = self.images + self.camera_preframes
            if self.projection_mode:
                sl = 1
                h_px = self.projection_height_px
            data_buffer = self._get_data_buffer((im, h_px, w_px), 'uint16')
            # camera.record_to_memory() blocks, so we use a thread:
            camera_thread = ct.ResultThread(
                target=self.camera.record_to_memory,
                kwargs={'allocated_memory': data_buffer,
                        'software_trigger': False},).start()
            # Race condition: the camera starts with (typically 16) single
            # frame buffers, which are filled by triggers from
            # ao.play_voltages(). The camera_thread empties them, hopefully
            # fast enough that we never run out. So far, the camera_thread
            # seems to both start on time, and keep up reliably once it starts,
            # but this could be fragile. The camera thread (effectively)
            # acquires shared memory as it writes to the allocated buffer.
            # On this machine the memory acquisition is faster than the camera
            # (~4GB/s vs ~1GB/s) but this could also be fragile if another
            # process interferes.
            self.ao.play_voltages(block=False)
            camera_thread.get_result()
            custody.switch_from(self.camera, to=self.datapreview)
            # Acquisition is 3D, but display and filesaving are 5D:
            data_buffer = data_buffer[ # ditch preframes
                self.camera_preframes:, :, :].reshape(vo, sl, ch, h_px, w_px)
            preview_buffer = self._get_preview_buffer(
                self.preview_shape, 'uint16')
            self.datapreview.get(
                data_buffer, pm, pa, s_um, s_px, l_px, c_px, ts,
                allocated_memory=preview_buffer)
            if display:
                custody.switch_from(self.datapreview, to=self.display)
                self.display.show_image(preview_buffer)
                custody.switch_from(self.display, to=None)
            else:
                custody.switch_from(self.datapreview, to=None)
            if filename is not None:
                data_path, preview_path = prepare_to_save_thread.get_result()
                if self.verbose:
                    print("%s: saving '%s'"%(self.name, data_path))
                    print("%s: saving '%s'"%(self.name, preview_path))
                # TODO: consider puting FileSaving in a SubProcess
                if not preview_only:
                    imwrite(data_path, data_buffer, imagej=True)
                imwrite(preview_path, preview_buffer, imagej=True)
                if self.verbose:
                    print("%s: done saving."%self.name)
            self._release_data_buffer(data_buffer)
            self._release_preview_buffer(preview_buffer)
            del preview_buffer
        acquire_thread = ct.CustodyThread(
            target=acquire_task, first_resource=self.camera).start()
        self.unfinished_tasks.put(acquire_thread)
        return acquire_thread

    def finish_all_tasks(self):
        collected_tasks = []
        while True:
            try:
                th = self.unfinished_tasks.get_nowait()
            except queue.Empty:
                break
            th.get_result()
            collected_tasks.append(th)
        return collected_tasks

    def close(self):
        if self.verbose: print("%s: closing..."%self.name)
        self.finish_all_tasks()
        self.ao.close()
        self.filter_wheel.close()
        self.camera.close()
        self.zoom_lens.close()
        self.lasers.close()
        self.focus_piezo.close()
        self.autofocus.close()
        self.XY_stage.close()
        self.Z_stage.close()
        self.Z_drive.close()
        self.display.close()
        if self.verbose: print("%s: done closing."%self.name)

class _CustomNapariDisplay:
    def __init__(self, auto_contrast=False):
        self.auto_contrast = auto_contrast
        self.viewer = napari.Viewer()

    def _legalize_slider(self, image):
        for ax in range(len(image.shape) - 2): # slider axes other than X, Y
            # if the current viewer slider steps > corresponding image shape:
            if self.viewer.dims.nsteps[ax] > image.shape[ax]:
                # set the slider position to the max legal value:
                self.viewer.dims.set_point(ax, image.shape[ax] - 1)

    def _reset_contrast(self, image): # 4D image min to max
        for layer in self.viewer.layers: # image, grid, tile
            layer.contrast_limits = (image.min(), image.max())

    def show_image(self, last_preview):
        self._legalize_slider(last_preview)
        if self.auto_contrast:
            self._reset_contrast(last_preview)
        if not hasattr(self, 'last_image'):
            self.last_image = self.viewer.add_image(last_preview)
        else:
            self.last_image.data = last_preview

    def show_grid_preview(self, grid_preview):
        if not hasattr(self, 'grid_image'):
            self.grid_image = self.viewer.add_image(grid_preview)
        else:
            self.grid_image.data = grid_preview

    def show_tile_preview(self, tile_preview):
        if not hasattr(self, 'tile_image'):
            self.tile_image = self.viewer.add_image(tile_preview)
        else:
            self.tile_image.data = tile_preview

    def close(self):
        self.viewer.close()

# HT SOLS definitions and API:

# The chosen API (exposed via '.apply_settings()') forces the user to
# select scan settings (via 'voxel_aspect_ratio' and 'scan_range_um') that are
# then legalized to give integer pixel shears when converting the raw data to
# the 'native' view data. This speeds up data processing and gives a natural or
# 'native' view of the data ***without interpolation***. If necessary an expert
# user can bypass these legalizers by directly setting the 'scan_step_size_px'
# and 'scan_range_um' attributes after the last call to '.apply_settings()'.

def calculate_scan_step_size_um(sample_px_um, scan_step_size_px):
    return scan_step_size_px * sample_px_um / np.cos(tilt)

def calculate_scan_range_um(sample_px_um, scan_step_size_px, slices_per_volume):
    scan_step_size_um = calculate_scan_step_size_um(
        sample_px_um, scan_step_size_px)
    return scan_step_size_um * (slices_per_volume - 1)

def calculate_voxel_aspect_ratio(scan_step_size_px):
    return scan_step_size_px * np.tan(tilt)

def calculate_cuboid_voxel_scan(
    sample_px_um, voxel_aspect_ratio, scan_range_um):
    scan_step_size_px = max(int(round(voxel_aspect_ratio / np.tan(tilt))), 1)
    scan_step_size_um = calculate_scan_step_size_um(
        sample_px_um, scan_step_size_px)
    slices_per_volume = 1 + int(round(scan_range_um / scan_step_size_um))
    return scan_step_size_px, slices_per_volume # watch out for fencepost!

class DataPreview:
    # Returns 3 max intensity projections along the traditional XYZ axes. For
    # speed (and simplicity) these are calculated to the nearest pixel (without
    # interpolation) and should propably not be used for rigorous analysis.
    @staticmethod
    def shape(projection_mode,
              projection_angle_deg,
              volumes_per_buffer,
              slices_per_volume,
              num_channels_per_slice, # = len(channels_per_slice)
              height_px,
              width_px,
              sample_px_um,
              scan_step_size_px,
              preview_line_px,
              preview_crop_px,
              timestamp_mode):
        # Calculate max pixel shear:
        scan_step_size_um = calculate_scan_step_size_um(
            sample_px_um, scan_step_size_px)
        prop_px_per_scan_step = scan_step_size_um / ( # for an O1 axis view
            sample_px_um * np.cos(tilt))
        prop_px_shear_max = int(np.rint(
            prop_px_per_scan_step * (slices_per_volume - 1)))
        # Get image size with projections:
        t_px, b_px = 2 * (preview_crop_px,) # crop top and bottom pixel rows
        if timestamp_mode == "binary+ASCII": t_px = 8 # ignore timestamps
        h_px = height_px - t_px - b_px
        x_px = width_px
        if projection_mode:
            y_px = int(round(h_px * np.sin( # h_px has galvo_shear_px added
                tilt + np.deg2rad(projection_angle_deg))))
            shape = (volumes_per_buffer,
                     num_channels_per_slice,
                     y_px,
                     x_px)
        else:
            y_px = int(round((h_px + prop_px_shear_max) * np.cos(tilt)))
            z_px = int(round(h_px * np.sin(tilt)))
            shape = (volumes_per_buffer,
                     num_channels_per_slice,
                     y_px + z_px + 2 * preview_line_px,
                     x_px + z_px + 2 * preview_line_px)
        return shape

    def get(self,
            data, # raw 5D data, 'tzcyx' input -> 'tcyx' output
            projection_mode,
            projection_angle_deg,
            sample_px_um,
            scan_step_size_px,
            preview_line_px,
            preview_crop_px,
            timestamp_mode,
            allocated_memory=None):
        vo, slices, ch, h_px, w_px = data.shape
        pm, pa = projection_mode, projection_angle_deg
        s_um, s_px = sample_px_um, scan_step_size_px
        l_px, c_px = preview_line_px, preview_crop_px
        # Get preview shape and check allocated memory (or make new array):
        preview_shape = self.shape(pm, pa, vo, slices, ch, h_px, w_px,
                                   s_um, s_px, l_px, c_px, timestamp_mode)
        if allocated_memory is not None:
            assert allocated_memory.shape == preview_shape
            return_value = None # use given memory and avoid return
        else: # make new array and return
            allocated_memory = np.zeros(preview_shape, 'uint16')
            return_value = allocated_memory
        t_px, b_px = 2 * (preview_crop_px,) # crop top and bottom pixel rows
        if timestamp_mode == "binary+ASCII": t_px = 8 # ignore timestamps
        prop_px = h_px - t_px - b_px # i.e. prop_px = h_px (with cropping)
        data = data[:, :, :, t_px:h_px - b_px, :]
        scan_step_size_um = calculate_scan_step_size_um(
            sample_px_um, scan_step_size_px)
        # Calculate max px shear on the propagation axis for an 'O1' projection:
        # -> more shear than for a 'native' projection
        prop_px_per_scan_step = scan_step_size_um / ( # O1 axis view
            sample_px_um * np.cos(tilt))
        prop_px_shear_max = int(np.rint(prop_px_per_scan_step * (slices - 1)))
        # Calculate max px shear on the scan axis for a 'width' projection:
        scan_steps_per_prop_px = 1 / prop_px_per_scan_step  # width axis view
        scan_px_shear_max = int(np.rint(scan_steps_per_prop_px * (prop_px - 1)))
        # Make projections:
        for v in range(vo):
            for c in range(ch):
                if projection_mode:
                    # Scale images according to pixel size (divide by X_px_um):
                    X_px_um = sample_px_um # width axis
                    Z_px_um = sample_px_um * np.sin( # prop. to O1 axis
                        tilt + np.deg2rad(pa))
                    img = data[v, 0, c, :, :] # 1 slice
                    proj_img  = zoom(
                        img, (Z_px_um / X_px_um, 1), mode='nearest')
                    # Make image with projection and flip for trad. view:
                    allocated_memory[v, c, :, :] = proj_img
                else:
                    O1_proj = np.zeros(
                        (prop_px + prop_px_shear_max, w_px), 'uint16')
                    width_proj = np.zeros(
                        (slices + scan_px_shear_max, prop_px), 'uint16')
                    max_width = np.amax(data[v, :, c, :, :], axis=2)
                    scan_proj = np.amax(data[v, :, c, :, :], axis=0)
                    for i in range(slices):
                        prop_px_shear = int(np.rint(i * prop_px_per_scan_step))
                        target = O1_proj[
                            prop_px_shear:prop_px + prop_px_shear, :]
                        np.maximum(target, data[v, i, c, :, :], out=target)
                    for i in range(prop_px):
                        scan_px_shear = int(np.rint(i * scan_steps_per_prop_px))
                        width_proj[scan_px_shear:slices + scan_px_shear, i] = (
                            max_width[:, i])
                    # Scale images according to pixel size (divide by X_px_um):
                    X_px_um = sample_px_um # width axis
                    Y_px_um = sample_px_um * np.cos(tilt) # prop. to scan axis
                    Z_px_um = sample_px_um * np.sin(tilt) # prop. to O1 axis
                    O1_img    = zoom(
                        O1_proj, (Y_px_um / X_px_um, 1), mode='nearest')
                    scan_img  = zoom(
                        scan_proj, (Z_px_um / X_px_um, 1), mode='nearest')
                    scan_scale = O1_img.shape[0] / width_proj.shape[0]
                    # = scan_step_size_um / X_px_um rounded to = O1_img.shape[0]
                    width_img = zoom(width_proj,
                                     (scan_scale, Z_px_um / X_px_um),
                                     mode='nearest')
                    # Make image with all projections and flip for trad. view:
                    y_px, x_px = O1_img.shape
                    line_min, line_max = O1_img.min(), O1_img.max()
                    # Pass projections into allocated memory:
                    m = allocated_memory # keep code short!
                    m[v, c, l_px:y_px + l_px, l_px:x_px + l_px] = np.flip(
                        O1_img)
                    m[v, c, y_px + 2*l_px:, l_px:x_px + l_px] = np.flip(
                        scan_img)
                    m[v, c, l_px:y_px + l_px, x_px + 2*l_px:] = np.flip(
                        width_img)
                    m[v, c, y_px + 2*l_px:, x_px + 2*l_px:] = np.full(
                        (scan_img.shape[0], width_img.shape[1]), 0)
                    # Add line separations between projections:
                    m[v, c, :l_px,    :] = line_max
                    m[v, c, :l_px, ::10] = line_min
                    m[v, c, y_px + l_px:y_px + 2*l_px,    :] = line_max
                    m[v, c, y_px + l_px:y_px + 2*l_px, ::10] = line_min
                    m[v, c, :,    :l_px] = line_max
                    m[v, c, ::10, :l_px] = line_min
                    m[v, c, :,    x_px + l_px:x_px + 2*l_px] = line_max
                    m[v, c, ::10, x_px + l_px:x_px + 2*l_px] = line_min
                    m[v, c, :] = np.flipud(m[v, c, :])
        return return_value

class DataZ:
    # Can be used to estimate the z location of the sample in um relative to
    # the lowest pixel (useful for software autofocus for example). Choose:
    # - 'max_intensity' to track the brightest z pixel
    # - 'max_gradient' as a proxy for the coverslip boundary
    def estimate(
        self,
        preview_image, # 2D preview image: single volume, single channel
        height_px,
        width_px,
        sample_px_um,
        preview_line_px,
        preview_crop_px,
        timestamp_mode,
        method='max_gradient',
        gaussian_filter_std=3,
        ):
        assert method in ('max_intensity', 'max_gradient')
        t_px, b_px = 2 * (preview_crop_px,) # crop top and bottom pixel rows
        if timestamp_mode == "binary+ASCII": t_px = 8 # ignore timestamps
        h_px = height_px - t_px - b_px
        z_px = int(round(h_px * np.sin(tilt))) # DataPreview definition
        inspect_me = preview_image[:z_px, preview_line_px:width_px]
        intensity_line = np.average(inspect_me, axis=1)[::-1] # O1 -> coverslip
        intensity_line_smooth = gaussian_filter1d(
            intensity_line, gaussian_filter_std) # reject hot pixels 
        if method == 'max_intensity':
            max_z_intensity_um = np.argmax(intensity_line_smooth) * sample_px_um
            return max_z_intensity_um
        intensity_gradient = np.zeros((len(intensity_line_smooth) - 1))
        for px in range(len(intensity_line_smooth) - 1):
            intensity_gradient[px] = (
                intensity_line_smooth[px + 1] - intensity_line_smooth[px])
        max_z_gradient_um = np.argmax(intensity_gradient) * sample_px_um
        return max_z_gradient_um

class DataRoi:
    # Can be used for cropping empty pixels from raw data. The HT-SOLS
    # microscope produces vast amounts of data very quickly, often with many
    # empty pixels (so discarding them can help). This simple routine assumes a
    # central sample/roi and then attemps to reject the surrounding empty pixels
    # accroding to the 'signal_to_bg_ratio' (threshold method).
    def get(
        self,
        data, # raw 5D data, 'tzcyx' input -> 'tzcyx' output
        preview_crop_px,
        timestamp_mode,
        signal_to_bg_ratio=1.2, # adjust for threshold
        gaussian_filter_std=3, # adjust for smoothing/hot pixel rejection
        ):
        vo, slices, ch, h_px, w_px = data.shape
        t_px, b_px = 2 * (preview_crop_px,) # crop top and bottom pixel rows
        if timestamp_mode == "binary+ASCII": t_px = 8 # ignore timestamps
        min_index_vo, max_index_vo = [], []
        for v in range(vo):
            min_index_ch, max_index_ch = [], []
            for c in range(ch):
                # Max project volume to images:
                width_projection = np.amax(
                    data[v, :, c, t_px:h_px - b_px, :], axis=2)
                scan_projection  = np.amax(
                    data[v, :, c, t_px:h_px - b_px, :], axis=0)
                # Max project images to lines and smooth to reject hot pixels:
                scan_line  = gaussian_filter1d(
                    np.max(width_projection, axis=1), gaussian_filter_std)
                prop_line  = gaussian_filter1d(
                    np.max(scan_projection, axis=1), gaussian_filter_std)
                width_line = gaussian_filter1d(
                    np.max(scan_projection, axis=0), gaussian_filter_std)
                # Find background level and set threshold:
                scan_threshold  = int(min(scan_line)  * signal_to_bg_ratio)
                prop_threshold  = int(min(prop_line)  * signal_to_bg_ratio)
                width_threshold = int(min(width_line) * signal_to_bg_ratio)
                # Estimate roi:.
                min_index_zyx = [0, 0, 0]
                max_index_zyx = [slices - 1, h_px - 1, w_px - 1]
                for i in range(slices):
                    if scan_line[i]  > scan_threshold:
                        min_index_zyx[0] = i
                        break
                for i in range(h_px - t_px - b_px):
                    if prop_line[i]  > prop_threshold:
                        min_index_zyx[1] = i + t_px # put cropped pixels back
                        break
                for i in range(w_px):
                    if width_line[i] > width_threshold:
                        min_index_zyx[2] = i
                        break        
                for i in range(slices):
                    if scan_line[-i] > scan_threshold:
                        max_index_zyx[0] = max_index_zyx[0] - i
                        break
                for i in range(h_px - t_px - b_px):
                    if prop_line[-i] > prop_threshold:
                        max_index_zyx[1] = max_index_zyx[1] - i - b_px
                        break
                for i in range(w_px):
                    if width_line[-i] > width_threshold:
                        max_index_zyx[2] = max_index_zyx[2] - i
                        break
                min_index_ch.append(min_index_zyx)
                max_index_ch.append(max_index_zyx)
            min_index_vo.append(np.amin(min_index_ch, axis=0))
            max_index_vo.append(np.amax(max_index_ch, axis=0))
        min_i = np.amin(min_index_vo, axis=0)
        max_i = np.amax(max_index_vo, axis=0)
        data_roi = data[
            :, min_i[0]:max_i[0], :, min_i[1]:max_i[1], min_i[2]:max_i[2]]
        return data_roi # hopefully smaller!

class DataNative:
    # The 'native view' is the most principled view of the data for analysis.
    # If 'type(scan_step_size_px) is int' (default) then no interpolation is
    # needed to view the volume. The native view looks at the sample with
    # the 'tilt' of the Snouty objective (microsope 3 in the emmission path).
    def get(
        self,
        data, # raw 5D data, 'tzcyx' input -> 'tzcyx' output
        scan_step_size_px):
        vo, slices, ch, h_px, w_px = data.shape
        prop_px = h_px # light-sheet propagation axis
        scan_step_px_max = int(np.rint(scan_step_size_px * (slices - 1)))
        data_native = np.zeros(
            (vo, slices, ch, prop_px + scan_step_px_max, w_px), 'uint16')
        for v in range(vo):
            for c in range(ch):
                for i in range(slices):
                    prop_px_shear = int(np.rint(i * scan_step_size_px))
                    data_native[
                        v, i, c, prop_px_shear:prop_px + prop_px_shear, :] = (
                            data[v, i, c, :, :])
        return data_native # larger!

class DataTraditional:
    # Very slow but pleasing - rotates the native view to the traditional view!
    def get(
        self,
        data_native, # raw 5D data, 'tzcyx' input -> 'tzcyx' output
        scan_step_size_px):
        vo, slices, ch, h_px, w_px = data_native.shape
        voxel_aspect_ratio = calculate_voxel_aspect_ratio(scan_step_size_px)
        tzcyx = []
        for v in range(vo):
            zcyx = []
            for c in range(ch):
                zyx_native_cubic_voxels = zoom(
                    data_native[v, :, c, :, :], (voxel_aspect_ratio, 1, 1))
                zyx_traditional = rotate(
                    zyx_native_cubic_voxels, np.rad2deg(tilt))
                zcyx.append(zyx_traditional[:, np.newaxis, : ,:])
            zcyx = np.concatenate(zcyx, axis=1)
            tzcyx.append(zcyx[np.newaxis, :, :, : ,:])
        data_traditional = np.concatenate(tzcyx, axis=0)
        return data_traditional # even larger!

# Convenience functions:

def prepend_datetime(string=''):
    dt = datetime.strftime(datetime.now(),'%Y-%m-%d_%H-%M-%S_')
    return dt + string

def get_multiwell_plate_positions(
    # Get a tuple of multiwell plate positions with a position string (label)
    # and XY positions (mm) based on a standard multiwell plate format. The
    # function is somewhat general to cater to both standard (e.g.
    # 96-well) plates or other formats, and can tile with a custom spacing
    # (e.g. set to 1 FOV for touching tiles, 0.9 FOV for 10% overlap or
    # 2x FOV to space out tiles). Use the 'start' and 'stop' args to define
    # a sub region and the A1 args to reset/recalibrate the location of A1.
    total_rows,                 # int   ( 8 for 96-well, 16 for 384-well)
    total_cols,                 # int   (12 for 96-well, 24 for 384-well)
    well_spacing_mm,            # float (4.5mm for 96-well, 9mm for 384-well)
    start,                      # string (A1, C3, etc)
    stop,                       # string (B2, F5, etc)
    tile_rows,                  # int (1, 2, 3 etc rows of adjacent tiles)
    tile_cols,                  # int (1, 2, 3 etc columns of adjacent tiles)
    tile_spacing_X_mm,          # float (X spacing between tiles mm, 1 FOV?)
    tile_spacing_Y_mm,          # float (Y spacing between tiles mm, 1 FOV?)
    A1_ul_X_mm,                 # float (A1 upper left X position mm)
    A1_ul_Y_mm,                 # float (A1 upper left Y position mm)
    A1_lr_X_mm,                 # float (A1 lower right X position mm)
    A1_lr_Y_mm,                 # float (A1 lower right Y position mm)
    ):
    # check total rows, cols and spacing:
    assert 1 <= total_rows <= 16, 'unexpected total_rows (%s)'%total_rows
    row_labels = tuple([chr(ord('A') + i) for i in range(total_rows)])
    assert 1 <= total_cols <= 24, 'unexpected total_cols (%s)'%total_cols
    col_labels = tuple(range(1, total_cols + 1))
    assert isinstance(well_spacing_mm, (int, float)), (
        'unexpected well_spacing_mm (%s)'%well_spacing_mm)
    # check start and stop:
    start_char, stop_char = start[0], stop[0]
    start_num, stop_num = int(start[1:]), int(stop[1:])
    assert start_char in row_labels, 'unexpected start_char (%s)'%start_char
    assert stop_char  in row_labels, 'unexpected stop_char (%s)'%stop_char
    assert start_num  in col_labels, 'unexpected start_num (%s)'%start_num
    assert stop_num   in col_labels, 'unexpected stop_num (%s)'%stop_num
    row_start = row_labels.index(start_char)
    row_stop  = row_labels.index(stop_char) + 1
    col_start = col_labels.index(start_num)
    col_stop  = col_labels.index(stop_num) + 1
    assert row_start <= row_stop, (
        'require row_start (%s) <= row_stop (%s)'%(row_start, row_stop))
    assert col_start <= col_stop, (
        'require col_start (%s) <= col_stop (%s)'%(col_start, col_stop))
    # check tiles and calculate offset to center them in the well:
    assert 1 <= tile_rows <= 100, 'tile_rows out of range (%s)'%tile_rows
    assert 1 <= tile_cols <= 100, 'tile_cols out of range (%s)'%tile_cols
    tile_offset_X_mm = 0.5 * (tile_rows - 1) * tile_spacing_X_mm
    tile_offset_Y_mm = 0.5 * (tile_cols - 1) * tile_spacing_Y_mm
    # check A1 positions and calculate center:
    assert isinstance(A1_ul_X_mm, (int, float)), (
        'unexpected A1_ul_X_mm (%s)'%A1_ul_X_mm)
    assert isinstance(A1_ul_Y_mm, (int, float)), (
        'unexpected A1_ul_Y_mm (%s)'%A1_ul_Y_mm)
    assert isinstance(A1_lr_X_mm, (int, float)), (
        'unexpected A1_lr_X_mm (%s)'%A1_lr_X_mm)
    assert isinstance(A1_lr_Y_mm, (int, float)), (
        'unexpected A1_lr_Y_mm (%s)'%A1_lr_Y_mm)
    A1_X_mm = A1_ul_X_mm - 0.5 * (A1_ul_X_mm - A1_lr_X_mm)
    A1_Y_mm = A1_ul_Y_mm - 0.5 * (A1_ul_Y_mm - A1_lr_Y_mm)
    # generate position array:
    multiwell_plate_positions = []
    for c in range(col_start, col_stop):
        rows_range = range(row_start, row_stop)
        if c % 2: # odd number: snake
            rows_range = reversed(rows_range)
        for r in rows_range: # move faster y-axis more frequently:
            for tc in range(tile_cols):
                tile_rows_range = range(tile_rows)
                if tc % 2:  # odd number: snake
                    tile_rows_range = reversed(tile_rows_range)
                for tr in tile_rows_range: # move faster y-axis more frequently:
                    position_string = '%s%02ir%02ic%02i'%(
                        row_labels[r], col_labels[c], tr, tc)
                    well_X_mm = A1_X_mm - c * well_spacing_mm # -ve for x
                    well_Y_mm = A1_Y_mm + r * well_spacing_mm # +ve for y
                    tile_X_mm =   tile_offset_X_mm - tc * tile_spacing_X_mm
                    tile_Y_mm = - tile_offset_Y_mm + tr * tile_spacing_Y_mm
                    XY_mm = (well_X_mm + tile_X_mm,
                             well_Y_mm + tile_Y_mm,
                             'absolute')
                    multiwell_plate_positions.append((position_string, XY_mm))
    return tuple(multiwell_plate_positions)

if __name__ == '__main__':
    t0 = time.perf_counter()

    # Create scope object:
    scope = Microscope(max_allocated_bytes=100e9, ao_rate=1e4)
    scope.apply_settings(       # Mandatory call
        projection_mode=False,
        projection_angle_deg=0,
        channels_per_slice=("LED", "488"),
        power_per_channel=(50, 10),
        emission_filter='ET525/50M',
        illumination_time_us=100,
        height_px=248,
        width_px=1060,
        voxel_aspect_ratio=2,
        scan_range_um=50,
        volumes_per_buffer=1,
##        autofocus_enabled=True, # optional test
        focus_piezo_z_um=(0,'relative'),
        XY_stage_position_mm=(0,0,'relative'),
        sample_ri=1.33,
        ls_focus_adjust_v=0,
        ls_angular_dither_v=0,
        ).get_result()

    # Run acquire:
    folder_name = prepend_datetime('ht_sols_test_data')
    for i in range(3):
        scope.acquire(
            filename='%06i.tif'%i,
            folder_name=folder_name,
            description='something...',
            display=True,
            preview_only=False,
            )
    scope.close()

    t1 = time.perf_counter()
    print('time_s', t1 - t0) # ~ 14s
