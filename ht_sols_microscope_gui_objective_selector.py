# Imports from the python standard library:
import tkinter as tk

# Our code, one .py file per module, copy files to your local directory:
import tkinter_compound_widgets as tkcw
import thorlabs_MCM3000

class ObjectiveSelector:
    def __init__(self,
                 which_port,
                 name='ObjectiveSelector',
                 verbose=True,
                 very_verbose=False):
        if verbose:
            print('%s: initializing'%name)
        # init hardware:
        ch = 2
        z_drive = thorlabs_MCM3000.Controller(
            which_port=which_port,
            stages=(None, None, 'ZFM2020'),
            reverse=(False, False, False),
            verbose=very_verbose)
        z0_um = round(z_drive.position_um[ch])
        O1_to_BFP_um = { # absolute positions of BFP's from alignment
            'Nikon 40x0.95 air'    : 0,
            'Nikon 40x1.15 water'  :-137,
            'Nikon 40x1.30 oil'    :-12023}
        O1_options = tuple(O1_to_BFP_um.keys())
        O1_BFP_um = tuple(O1_to_BFP_um.values())
        # check position:
        p0 = None
        if z0_um in O1_BFP_um:
            p0 = O1_BFP_um.index(z0_um)
            if verbose:
                print('%s: current position   = %s'%(name, O1_options[p0]))
        # init gui:
        root = tk.Tk()
        root.title('Objective selector GUI')        
        # objective selector:
        def _move(rb_pos):
            if verbose:
                print('%s: moving to position = %s'%(name, O1_options[rb_pos]))
            z_drive.move_um(ch, O1_BFP_um[rb_pos], relative=False)
            if verbose:
                print('%s: -> done.'%name)
            return None
        O1 = tkcw.RadioButtons(root,
                               label='OBJECTIVE SELECTOR',
                               buttons=O1_options,
                               default_position=p0,
                               function=_move)
        # quit:
        def _quit():
            if verbose:
                print('%s: closing'%name)
            z_drive.close()
            root.quit()
            if verbose:
                print('%s: -> done.'%name)
            return None
        button_quit = tk.Button(
            root, text="QUIT", command=_quit, height=3, width=20)
        button_quit.grid(row=1, column=0, padx=20, pady=20, sticky='n')
        if verbose:
            print('%s: -> done.'%name)
        # run gui:
        root.mainloop()
        root.destroy()

if __name__ == '__main__':
    objective_selector = ObjectiveSelector(
        which_port='COM21', verbose=True, very_verbose=False)
