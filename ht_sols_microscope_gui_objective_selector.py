# Imports from the python standard library:
import tkinter as tk

# Our code, one .py file per module, copy files to your local directory:
import prior_PureFocus850       # github.com/amsikking/prior_PureFocus850
import thorlabs_MCM3000                 # github.com/amsikking/thorlabs_MCM3000
import ht_sols_microscope as ht_sols # github.com/amsikking/HT_SOLS_microscope
import tkinter_compound_widgets as tkcw # github.com/amsikking/tkinter 

class ObjectiveSelector:
    def __init__(self,
                 name='ObjectiveSelector',
                 verbose=True,
                 very_verbose=False):
        if verbose:
            print('%s: initializing'%name)
        # init hardware:
        autofocus = prior_PureFocus850.Controller(
            which_port='COM6', verbose=very_verbose)
        ch = 2
        z_drive = thorlabs_MCM3000.Controller(
            which_port='COM9',
            stages=(None, None, 'ZFM2020'),
            reverse=(False, False, False),
            verbose=very_verbose)
        z_um = round(z_drive.position_um[ch])
        O1_options = ht_sols.objective1_options['name']
        O1_positions_um = ht_sols.objective1_options['BFP_um']
        # check position:
        p0 = None
        if z_um in O1_positions_um:
            p0 = O1_positions_um.index(z_um)
            if verbose:
                print('%s: current position   = %s'%(name, O1_options[p0]))
        if verbose:
            print('%s: -> done.'%name)
        # init gui:
        root = tk.Tk()
        root.title('Objective selector GUI')        
        # objective selector:
        def _move(rb_pos):
            if verbose:
                print('%s: moving to position = %s'%(name, O1_options[rb_pos]))
            autofocus.set_current_objective(rb_pos + 1)
            z_drive.move_um(ch, O1_positions_um[rb_pos], relative=False)
            if verbose:
                print('%s: -> done.'%name)
            return None
        O1 = tkcw.RadioButtons(root,
                               label='OBJECTIVE SELECTOR',
                               buttons=O1_options,
                               default_position=p0,
                               function=_move)
        # add close function + any commands for when the user hits the 'X'
        def _close():
            if verbose:
                print('%s: closing'%name)
            autofocus.close()
            z_drive.close()
            root.destroy()
            if verbose:
                print('%s: -> done.'%name)
            return None
        root.protocol("WM_DELETE_WINDOW", _close)
        # run gui:
        root.mainloop() # blocks here until 'X'

if __name__ == '__main__':
    objective_selector = ObjectiveSelector(verbose=True, very_verbose=False)
