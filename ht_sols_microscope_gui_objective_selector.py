import tkinter as tk

import tkinter_compound_widgets as tki_cw
import thorlabs_MCM3000

class ObjectiveSelector:
    def __init__(self, master, name='ObjectiveSelector'):
        self.master = master
        self.name = name
        print('%s: initializing'%self.name)        
        self.z_drive_ch = 2
        self.z_controller = thorlabs_MCM3000.Controller(
            'COM12',
            stages=(None, None, 'ZFM2020'),
            reverse=(False, False, False),
            verbose=False)
        self.objectives = (
            'Nikon 40x0.95 air', 'Nikon 40x1.15 water', 'Nikon 40x1.30 oil')
        self.objective_BFP_um = ( # absolute position from alignment
            0, -137, -12023)
        start_z_um = self.z_controller.position_um[self.z_drive_ch]
        frame = tk.LabelFrame(master, text='OBJECTIVE SELECTOR', bd=6)
        frame.grid(row=0, column=0, rowspan=1, padx=20, pady=20, sticky='n')
        self.objective = tki_cw.RadioButtons(
            frame,
            label='options',
            buttons=self.objectives,
            function=self.function)
        quit_button = tk.Button(
            root, text="QUIT", command=self.quit, height=5, width=30)
        quit_button.grid(row=1, column=0, padx=20, pady=20, sticky='n')
        print('%s: -> done.'%self.name)
        if round(start_z_um) in self.objective_BFP_um: # check position
            start_position = self.objective_BFP_um.index(round(start_z_um))
            self.objective.tk_position.set(start_position)
            print('%s: current position   = %s'%(
                self.name, self.objectives[start_position]))

    def function(self, rb_pos):
        print('%s: moving to position = %s'%(
            self.name, self.objectives[rb_pos]))
        self.z_controller.move_um(
            self.z_drive_ch, self.objective_BFP_um[rb_pos], relative=False)
        print('%s: -> done.'%self.name)

    def quit(self):
        print('%s: closing'%self.name)
        self.z_controller.close()
        self.master.quit()
        print('%s: -> done.'%self.name)

if __name__ == '__main__':
    root = tk.Tk()
    root.title('HT SOLS Microscope GUI')
    objective_selector = ObjectiveSelector(root)
    root.mainloop()
    root.destroy()
