# HT_SOLS_microscope
Python control code for the first 'high throughput single-objective light-sheet' (HT-SOLS) microscope.

## Key features:
A fast, gentle, large field of view, 3D fluorescence microscope using the single-objective light-sheet ([SOLS](https://andrewgyork.github.io/high_na_single_objective_lightsheet/)) architecture, with an [AMS-AGY v2.0 objective](https://andrewgyork.github.io/high_na_single_objective_lightsheet/appendix.html#AMS-AGY_v2.0) (a.k.a Snouty). This microscope includes the any-immersion remote focus ([AIRR](https://amsikking.github.io/any_immersion_remote_refocus_microscopy/)) technology and real time multi-angle [projections](https://doi.org/10.1038/s41592-021-01175-7).
- **Primary objectives**: 40x air, water and oil immersion (correction collars for air and water).
- **XYZ field of view**: ~300x300x100um.
- **Excitation**: fast lasers with 405, 488, 561, 640 (nm).
- **Light-sheet**: Gaussian with powell lens, automated adjustment.
- **Emission**: fast filter wheel with ET445/58M, ET525/50M, ET600/50M, ET706/95M and ZET405/488/561/640m filters.
- **Course/fine focus**: automated z stage and fast piezo.
- **XY stage**: fast piezo walking.
- **Scanning/projections**: fast galvos.
- **Autofocus**: hardware based.
- **Incubation**: stage top temperature and CO2 can be used.
- **Control**: basic GUI and open source API in Python.

![social_preview](https://github.com/amsikking/HT_SOLS_microscope/blob/main/social_preview.png)
