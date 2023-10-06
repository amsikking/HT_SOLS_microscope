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
        self.name = name
        self.verbose = verbose
        self.root = tk.Tk()
        self.root.title('HT SOLS Microscope GUI')
        if self.verbose:
            print('%s: initializing'%self.name)        
        self.z_drive_ch = 2
        self.z_controller = thorlabs_MCM3000.Controller(
            which_port=which_port,
            stages=(None, None, 'ZFM2020'),
            reverse=(False, False, False),
            verbose=very_verbose)
        self.objectives = (
            'Nikon 40x0.95 air', 'Nikon 40x1.15 water', 'Nikon 40x1.30 oil')
        self.objective_BFP_um = ( # absolute position from alignment
            0, -137, -12023)
        start_z_um = self.z_controller.position_um[self.z_drive_ch]
        frame = tk.LabelFrame(self.root, text='OBJECTIVE SELECTOR', bd=6)
        frame.grid(padx=20, pady=20)
        self.objective = tkcw.RadioButtons(
            self.root,
            label='options',
            buttons=self.objectives,
            function=self.function)
        quit_button = tk.Button(
            self.root, text="QUIT", command=self.quit, height=3, width=20)
        quit_button.grid(row=1, column=0, padx=20, pady=20, sticky='n')
        if self.verbose:
            print('%s: -> done.'%self.name)
        if round(start_z_um) in self.objective_BFP_um: # check position
            start_position = self.objective_BFP_um.index(round(start_z_um))
            self.objective.position.set(start_position)
            if self.verbose:
                print('%s: current position   = %s'%(
                    self.name, self.objectives[start_position]))
        self.root.mainloop()
        self.root.destroy()

    def function(self, rb_pos):
        if self.verbose:
            print('%s: moving to position = %s'%(
            self.name, self.objectives[rb_pos]))
        self.z_controller.move_um(
            self.z_drive_ch, self.objective_BFP_um[rb_pos], relative=False)
        if self.verbose:
            print('%s: -> done.'%self.name)
        return None

    def quit(self):
        if self.verbose:
            print('%s: closing'%self.name)
        self.z_controller.close()
        self.root.quit()
        if self.verbose:
            print('%s: -> done.'%self.name)
        return None

if __name__ == '__main__':
    objective_selector = ObjectiveSelector(
        which_port='COM21', verbose=True, very_verbose=False)
