from os.path import dirname
kgui_dir = dirname(__file__)

large_font = 34
normal_font = 21
small_font = 17
extra_small_font = 14
screen_width = 600
screen_height = 1024
radius = 5
popup_radius = 14

status_bar_height = 22
progress_bar_height = 5
btn_height = 64
tab_height = 100
#generate even spacing based on all contents on homescreen
padding = (screen_height - tab_height - progress_bar_height-normal_font - status_bar_height - btn_height*7)/float(10)
home_col_height = padding+btn_height
padding_hint = padding/screen_width
small_padding = 15

background = [0.07,0.07,0.07,1]
popup = [0.15,0.15,0.15,1]
divider = [1,1,1,0.1]
button = [1,1,1,0.08]
button_outline = [1,1,1,0.1]
medium_gray = [0.2,0.2,0.2,1]
medium_light_gray = [0.35,0.35,0.35,1]
light_gray = [0.7,0.7,0.7,1]
translucent_white = [1,1,1,0.1]
accent = [0,0.76,0.9,1]
status_bar = accent
red = [0.9,0,0,1]

notify_info = accent[:3] + [0.75]
notify_warning = [0.75, 0.25, 0, 0.75]
notify_error = [0.7, 0, 0, 1]
notify_success = [0, 0.7, 0, 0.75]

"""
Kivy Guide
LABELS: try setting hints to None if it does shit e.g. setting size doesnt work
    size: outer dimensions of the label widget, available space, can be set to texture_size to show everything 
    text_size: can be set to limit texture size e.g. cut off text, can be set to size to show all that fits, 
    texture_size: size of the actual text not cut off(outer dimensions), can set font_size
    always size_hint: None, None when setting size: needed
in canvas: e.g. self.*** acceses the 'parent' widget of the canvas, unlike in other child Instances like Label:
pos: coordinates are always relative to the innermost Layout, not Widget you are in
Widgets: always define size first then pos at least when using top or right.. x:
Never Put comments after canvas: Instruction
How to access Instances or their methods:
    in kv to on_propertychange: id.method() id can be bound within root widget
    in py someinstance.bind(someinstances on_propertychange = self.method_to_bind) passes instance and every property
    by instantiating in python, storing instance
    in python self.ids["id"].method() instances of child widges can be accessed by id (ids is dict with instance as value)
    get_id(instance) sloow
    get_instance(id) also slow
"""



