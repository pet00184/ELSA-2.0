import pandas as pd
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtMultimedia import QSoundEffect
from PyQt6.QtCore import QUrl
import PyQt6
from pyqtgraph import PlotWidget, plot
import pyqtgraph as pg
import sys
import os
import numpy as np
import GOES_data_upload as GOES_data
import flare_conditions as fc
import emission_measure
from datetime import datetime, timedelta, timezone
import math

PACKAGE_DIR = os.path.dirname(os.path.realpath(__file__))

class RealTimeTrigger(QtWidgets.QWidget):
    
    print_updates=False #prints more updated in terminal. Only suggested for real-time data.
    ms_timing = 4000 #amount of ms between each new data download.
    
    LAUNCH_TO_FOXSI_OBS_START = 2
    LAUNCH_TO_FOXSI_OBS_END = LAUNCH_TO_FOXSI_OBS_START + 6
    LAUNCH_TO_HIC_OBS_START = LAUNCH_TO_FOXSI_OBS_START + 1
    LAUNCH_TO_HIC_OBS_END = LAUNCH_TO_HIC_OBS_START + 6
    DEADTIME = 30
    
    # Keeping all of the triggers in one spot, which will be called when initializing all the GOES and EVE plots. These are the same colors/lines for both, so I'm keeping them at the beginning.
    standard_events = [
        ('Data Trigger', 'gray'),
        ('Actual time of Trigger', 'k'),
        ('FOXSI Launch', 'green'),
        ('HIC Launch', 'orange')
    ]
    
    # need to be class variable to connect
    value_changed_signal_status = QtCore.pyqtSignal()
    value_changed_new_xrsb = QtCore.pyqtSignal()
    value_changed_alerts = QtCore.pyqtSignal()

    def __init__(self, goes_data, eve_data, foldername, sound_filename, no_eve, test_trigger=False, parent=None):
        super().__init__(parent)

        #### basic setup ####
        self.no_eve = no_eve
        self.XRS_data = goes_data
        self.EVE_data = eve_data
        self.foldername = foldername
        self._setup_folder()
        self.sound_filename=sound_filename
        self._setup_sound()
        
        self._init_flare_state()
        self._setup_flaresummary()
        
        #initial loading of the data: 
        self.load_data(reload=False)
        if self.no_eve==False:
            self.load_eve_data(reload=False)
        
        #initializing the plot styles and layout: 
        self._setup_plot_layout()
        self._setup_plot_styles(self.graphWidget, 'W m<sup>-2</sup>', 'GOES XRS')
        self._setup_plot_styles(self.tempgraph, 'MK', 'Temperature')
        self._setup_plot_styles(self.emgraph, 'cm<sup>-3</sup>', 'Emission Measure')
        self._setup_plot_styles(self.evegraph0, 'Raw Counts', 'EVE ESP 0-7nm')

        #initializing the display for all plots (this is mainly fixing the axis ranges)
        self._initialize_display()
            
        #PLOTTING GOES, EVE and all the event lines
        self._setup_GOES_plotdata()
        self._setup_EVE_plotdata()
            
        #Defining the flare trigger
        self._setup_trigger(test_trigger)
        
        #updating data!!
        self._start_timer()


################################## FUNCTIONS CALLED DIRECTLY IN __INIT__ ###################

    def _setup_folder(self):
        """Makes the SessionSummaries Folder to save flare and FAI summaries."""
        path = os.path.join(PACKAGE_DIR, "SessionSummaries", self.foldername)
        os.makedirs(path, exist_ok=True)

    def _setup_sound(self):
        """Creates the sound effect widget."""
        self.trigger_sound_effect = QSoundEffect()
        self.trigger_sound_effect.setSource(QUrl.fromLocalFile(self.sound_filename))
        self.trigger_sound_effect.setLoopCount(1)

    def _init_flare_state(self):
        """Setting up the XRS variables that will be filled when data is loaded, as well as setting the flare state to be ready to search!"""
        #defining XRS variables: 
        self.goes_current = None #newly reloaded data
        self.goes = None #total data (aggregated during entire run time)
        self.current_time = None #most recent time of data
        self.current_realtime = self._get_datetime_now() #current realtime- accounts for 3 minute latency
        #defining flare state 
        self.flare_prediction_state("searching")
        self.flare_happening = False
        self.launch = False
        self.post_launch = False

    def _setup_flaresummary(self):
        """setting up the flare summary and FAI data to be saved after each run."""
        self.flare_summary = pd.DataFrame(columns=['Trigger','Realtime Trigger', 'Countdown Initiated', 'Hold', 'Launch', 'HiC Launch', 'Flare End', 'FOXSI Obs Start', 'FOXSI Obs End', 'HiC Obs Start', 'HiC Obs End'])
        self.flare_summary_index = -1
        self.fai_summary = pd.DataFrame(columns=['Flare_Index', 'FAI_Time'])
        self.fai_summary_index = -1
        self.FAI_loc = 0

    def _setup_plot_layout(self):
        """Setups up the plot widgets for each of the four plots and defines their layout."""
        self.layout = QtWidgets.QGridLayout()
        
        self.graphWidget = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem()}) # to have the plot at UTC then `pg.DateAxisItem(utcOffset=0)`
        self.tempgraph = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem()})
        self.emgraph = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem()})
        self.evegraph0 = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem()})
        
        #THIS IS WHERE YOU CAN CHANGE THE LAYOUT IF YOU WANT
        self.layout.addWidget(self.graphWidget, 0, 0, 1, 3)
        self.layout.addWidget(self.tempgraph, 0, 3, 1, 2)
        self.layout.addWidget(self.emgraph, 1, 3, 1, 2)
        self.layout.addWidget(self.evegraph0, 1, 0, 1, 3)
            
        self.setLayout(self.layout)

    def _setup_plot_styles(self, plotwidget, y_label, title):
        """Defines the background color, labels and title for the specified plotwidget."""
        plotwidget.setMouseEnabled(x=False, y=False)  # Disable mouse panning & zooming
        
        plotwidget.setBackground('w')
        styles = {'color':'k', 'font-size':'20pt', "units":None} 
        plotwidget.setLabel('left', y_label, **styles)
        plotwidget.setLabel('bottom', 'Time', **styles)
        plotwidget.setTitle(title, color='k', size='24pt')
        plotwidget.addLegend()
        plotwidget.showGrid(x=True, y=True)
        plotwidget.getAxis('left').enableAutoSIPrefix(enable=False)

    def _setup_GOES_plotdata(self):
        """Function that will do the initial plotting of all the GOES plot lines and the event lines. First, dictionaries are defined so that the plotting can
        be looped through more easily. All of the plots are then saved to a dictionary self.plots, so that hopefully I can much more easily update data/lines later."""
        #all GOES data will have the same times
        self.time_tags = [pd.Timestamp(date).timestamp() for date in self.goes['time_tag']]

        # Making a dictionary to keep all of the GOES plot configurations and ylims for the line heights (making this global bc we might want to update the height?? or is that in display..... hmmmmm)
        self.goes_plot_configs = {
            'GOES': {
                'widget': self.graphWidget,
                'data': {
                    'XRSA': ('b', np.array(self.goes['xrsa'])),
                    'XRSB': ('r', np.array(self.goes['xrsb']))
                },
                'event_range': [1e-9, 1e-3],
                'logy': self._logy,
                'hard_limits': (-8 * 1.02, -3 * 0.96)  # log-space y-lims
            },
            'Temp': {
                'widget': self.tempgraph,
                'data': {
                    'Temp': ('g', np.array(self.goes['Temp']))
                },
                'event_range': [self.line_min_temp, self.line_max_temp],
                'logy': False,
                'hard_limits': [2, 18]
            },
            'EM': {
                'widget': self.emgraph,
                'data': {
                    'EM': ('orange', np.array(self.goes['emission measure']))
                },
                'event_range': [self.line_min_em, self.line_max_em],
                'logy': False,
                'hard_limits': None
            }
        }

        # Dictionary to hold all plots!!
        self.plots = {}
        self.initial_plotting_loop(self.goes_plot_configs, self.plots, self.time_tags)

    def _setup_EVE_plotdata(self):
        """The same idea as _setup_GOESplotdata, just with EVE! There will be one of these for each data source we are interested in."""
        if self.no_eve:
            self.create_noeve_plot()
        else:
            self.evetime_tags = [pd.Timestamp(str(date)).timestamp() for date in self.eve['UTC_TIME']]

            self.eve_plot_configs = {
                'EVE0': {
                    'widget': self.evegraph0,
                    'data': {
                        'EVE0': ('salmon', self.eve['ESP_0_7_COUNTS'])
                    },
                    'event_range': [self.line_min_eve0, self.line_max_eve0],
                    'logy': self._logy,
                    'hard_limits': None
                }
            }

            #Also setting up a dictionary to hold the EVE plots
            self.eve_plots = {}
            self.initial_plotting_loop(self.eve_plot_configs, self.eve_plots, self.evetime_tags)

    def _setup_trigger(self, test_trigger):
        # alerts *** DO NOT forget to end both tuples with `,`
        # add new alerts to `update_flare_alerts()` as well
        if test_trigger:
            self.flare_alert_map = fc.FLARE_ALERT_MAP_NEW
        else:
            self.flare_alert_map = fc.FLARE_ALERT_MAP
        self.flare_alert_names = tuple(self.flare_alert_map.keys())
        self.flare_alerts = pd.DataFrame(data={n:[False] for n in self.flare_alert_names}, index=["states"])

    def _start_timer(self):
        """Starts the QTimer, which is what is continuously updating the system!"""
        self.timer = QtCore.QTimer()
        self.timer.setInterval(self.ms_timing)
        self.timer.timeout.connect(self._update)
        self.timer.start()


    
    ###################### PLOTTING SCRIPTS USED BY SETUP FUNCTIONS ########################
    
    def plot_data(self, plotwidget, x, y, color, plotname, symbol=True, log=False, width=5):
        """Standard data plotting that can be used for initial plotting of both the data streams themselves and the lines. This will only be used at the
        beginning, as SetData is used when updating the data as it is continuously loaded. """
        pen = pg.mkPen(color=color, width=width)
        ydata = self._log_data(y) if log else y
        if symbol:
            return plotwidget.plot(x, ydata, name=plotname, pen=pen, symbol="o", symbolSize=3)
        else:
            return plotwidget.plot(x, ydata, name=plotname, pen=pen)
        
    def create_event_lines(self, plotwidget, time_start, y_range, events, fai_loc=None, fai_time=None):
        """Standard line plotting to be used for initial plotting of all the event lines. This will only be used at the beginning, as SetData is used
        when updating the line locations."""
        plots = {}
        for name, color in events:
            plot_item = self.plot_data(plotwidget, [time_start]*2, y_range, color, name, symbol=False)
            plot_item.setAlpha(0, False)
            plots[name] = plot_item

        # There might be an FAI with past data! 
        if fai_loc is not None and fai_time is not None:
            fai_plot = self.plot_data(plotwidget, [fai_time]*2, y_range, 'pink', 'FAI', symbol=False)
            fai_plot.setAlpha(1 if fai_loc > 0 else 0, False)
            plots['FAI'] = fai_plot

        return plots
    
    def initial_plotting_loop(self, dataconfig_dictionary, plot_dictionary, timetag):
        """For one of the dataconfig_dictionaries created in the _setup_x_plotdata functions, loop through all of the data types, making the data plots and the lines. 
        Then, each of these are saved to the specified plot dictionary.
        
        Input:
        -------------------------
        dataconfig_dictionary (dict) = dictionary for a specific data type (either GOES or EVE) that defines all the plots being made from that data source. 
                                        These are defined in _setup_X_plotdata.
        plot_dictionary (dict) = empty dictionary also defined in _setup_X_plotdata that will be populated with both the data and event lines. There will be one of these
                                        dictionaries for each data source.
        timetag (arr of datetimes) = Array of the times, used for the x-axis plotting. These are also different for each data source.
        
        """
        for plot_type, config in dataconfig_dictionary.items():
            widget = config['widget']
            y_range = config['event_range']

            # Main data plots
            for name, (color, data) in config['data'].items():
                plot_dictionary[name] = self.plot_data(widget, timetag, data, color, plotname=name)

            # See if there is an FAI and then plot all of the lines
            fai_time = pd.Timestamp(self.goes['time_tag'].iloc[self.FAI_loc]).timestamp() if self.FAI_loc >= 0 else None
            plot_dictionary.update(
                self.create_event_lines(widget, timetag[0], y_range, self.standard_events, fai_loc=self.FAI_loc, fai_time=fai_time)
            )
    
    def create_noeve_plot(self):
        font = QtGui.QFont()
        font.setPixelSize(40)
        self.evegraph0.setYRange(0, 1)
        self.evetext = pg.TextItem("No EVE Data", color=(255,0,0), anchor=(0.5,0.5))
        self.evegraph0.addItem(self.evetext)
        xloc = pd.Timestamp(self._get_datetime_now()-timedelta(minutes=15)).timestamp()
        self.evetext.setPos(xloc, .5)
        self.evetext.setFont(font)

########################## DISPLAY FUNCTIONS ######################################
    def _initialize_display(self):
        """This is mostly a placeholder rn so that it does everything it is supposed to do in the __init__ function. Will edit all of this into something neater!"""
        # convert left and right y-axes to display GOES notation stuff
        self._min_arr, self._max_arr = "xrsa", "xrsb" # give values to know what ylims are used
        self._logy = True
        self._lowest_yrange, self._highest_yrange = -8*1.02, -3*0.96
        self.display_goes()
        self.display_temp()
        self.display_em()
        self.display_eve0()
        self.xlims()

def _goes_strings(self, cls, arng, append=""):
    """ GenerateGOES class strings."""
    return [cls+str(v)+append for v in arng]

def display_goes(self):
    """ Method to add in the GOES class stuff"""
    
    log_value = np.arange(-10,-1) # get the letter class log-values
    value = 10**(log_value.astype(float)) # get the letter class values
    
    intermediate_classes = [1,2,3,4,5,6,7,8,9]
    num_of_int = [value[None,:] for _ in range(len(intermediate_classes))]
    value_ints = (np.vstack(num_of_int).T*np.array(intermediate_classes)).flatten() # now go letter class, half up, next letter class; e.g., A, A5, B, B5, etc.
    log_value_ints = self._log_data(value_ints)
    
    goes_labels_ints = self._goes_strings("A0.0", arng=intermediate_classes)+\
                        self._goes_strings("A0.", arng=intermediate_classes)+\
                        self._goes_strings("A", arng=intermediate_classes)+\
                        self._goes_strings("B", arng=intermediate_classes)+\
                        self._goes_strings("C", arng=intermediate_classes)+\
                        self._goes_strings("M", arng=intermediate_classes)+\
                        self._goes_strings("X", arng=intermediate_classes)+\
                        self._goes_strings("X", arng=intermediate_classes, append="0")+\
                        self._goes_strings("X", arng=intermediate_classes, append="00")

    # set the y-limits for the plot
    self.ylims()

    # do axis stuff, show top line and annotate right axis
    self.graphWidget.showAxis('top')
    self.graphWidget.getAxis('top').setStyle(showValues=False)
    self.graphWidget.getAxis('top').setGrid(False)
    self.graphWidget.showAxis('right')
    #self.graphWidget.getAxis('right').setLabel('GOES Class')
    self.graphWidget.getAxis('right').setGrid(False)
    self.graphWidget.getAxis('right').enableAutoSIPrefix(enable=False)

    keep_intermediate_classes = self.ticks_display()

    goes_labels_ints_keep = self._keep_goes_intermediate(intermediate_classes=intermediate_classes, classes_to_keep=keep_intermediate_classes)
    goes_value_ints_keep = log_value_ints[goes_labels_ints_keep]

    if self._logy:
        self.graphWidget.getAxis('right').setTicks([[(v, str(s)) if (v in goes_value_ints_keep) else (v,"") for v,s in zip(log_value_ints,goes_labels_ints)]])
        self.graphWidget.getAxis('left').setTicks([[(v, f"{s:0.0e}") if (v in goes_value_ints_keep) else (v,"") for v,s in zip(log_value_ints,value_ints)]])
    else: 
        self.graphWidget.getAxis('right').setTicks([[(v, str(s)) if (v in goes_value_ints_keep) else (v,"") for v,s in zip(value_ints,goes_labels_ints)]])
        self.graphWidget.getAxis('left').setTicks([[(v, f"{s:0.0e}") if (v in goes_value_ints_keep) else (v,"") for v,s in zip(value_ints,value_ints)]])
    
    self.xlims()

def ticks_display(self):
    """ Chooses which ticks to display for certain y-ranges. """
    _max_arr = getattr(self,"new_"+self._max_arr) if hasattr(self, "new_"+self._max_arr) else self.goes['xrsb']
    _a = 1.1 if self._logy else np.nanmax(_max_arr[np.isfinite(_max_arr)])*0.9
    _b = 2.1 if self._logy else np.nanmax(_max_arr[np.isfinite(_max_arr)])*1.5

    if (self.upper-self.lower)<=_a:
        keep_intermediate_classes = [1,2,3,4,5,6,7,8,9]
    elif _a<(self.upper-self.lower)<=_b:
        keep_intermediate_classes = [1,2,4,6,8]
    else:
        keep_intermediate_classes = [1]
    return keep_intermediate_classes

def _keep_goes_intermediate(self, intermediate_classes, classes_to_keep):
    """ Work out which intermediate GOES class to plot the tick labels for. """
    return [(np.array(classes_to_keep)-1)+i*len(intermediate_classes) for i in range(9)]

def ylims(self):
    """ 
    The ylims are:
        ymin = A-class or half an order of magnitude below the min. of `self._min_arr`.
        ymax = X10-class or half an order of magnitude above the max. of `self._max_arr`.

    The ylims are, by DEFAULT:
        ymin = A-class or half an order of magnitude below the min. of XRSA.
        ymax = X10-class or half an order of magnitude above the max. of XRSB.
    """
    _supported_arrays = ["xrsa", "xrsb"]
    if (self._min_arr not in _supported_arrays) or (self._max_arr not in _supported_arrays):
        print(f"self._min_arr={self._min_arr} or self._max_arr={self._max_arr} not in _supported_arrays={_supported_arrays}.")
        return
    
    # make sure arrays are the most recent
    _min_arr = getattr(self,"new_"+self._min_arr) if hasattr(self, "new_"+self._min_arr) else self.goes['xrsa']
    _max_arr = getattr(self,"new_"+self._max_arr) if hasattr(self, "new_"+self._max_arr) else self.goes['xrsb']

    # define, in log space, the top and bottom y-margin for the plotting
    _ymargin = 0.25 if self._logy else np.nanmin(_min_arr[np.isfinite(_min_arr)])

    # depend plotting on lowest ~A1 (slightly less to make sure tick plots)
    _lyr = self._lowest_yrange if self._logy else 10**self._lowest_yrange
    self.lower = np.nanmax([_lyr, self._log_data(np.nanmin(_min_arr[np.isfinite(_min_arr)]))-_ymargin]) # *1.02 to make sure lower tick for -8 actually appears if needed
    # on 200x largest xsrb value to look sensible and scale with new data
    _hyr = self._highest_yrange if self._logy else 10**self._highest_yrange
    self.upper = np.nanmin([self._log_data(np.nanmax(_max_arr[np.isfinite(_max_arr)]))+_ymargin, _hyr]) # *0.96 to make sure upper tick for -3 actually appears if needed
    self.graphWidget.plotItem.vb.setLimits(yMin=self.lower, yMax=self.upper)
    self.graphWidget.plot() # update the plot with the new ylims     

def _log_data(self, array):
    """ Check if the data is to be logged with `self._logy`."""
    if self._logy:
        log = np.log10(array)
        return log
    return array



### updating!!! HAPPY WITH ALL OF THIS DOWN TO NEXT ##########################
def _configure_axes(self, widget, y_log=False):
    """Uniformly configure axes appearance and log mode. This is the same fof all plots."""
    widget.showAxis('top')
    widget.getAxis('top').setStyle(showValues=False)
    widget.getAxis('top').setGrid(False)
    
    widget.showAxis('right')
    widget.getAxis('right').setGrid(False)
    widget.getAxis('right').enableAutoSIPrefix(enable=False)
    
    widget.setLogMode(x=False, y=y_log)

def _set_y_limits(self, widget, data, buffer=(0.9, 1.1), event_buffer=(0.8, 1.2), hard_min=None, hard_max=None):
    """Compute min/max and set limits for a plot widget.
    Input:
    -----------------------
    widget = specified plot widget
    data = data being plotted for that widget
    buffer = buffer for the data itself, so that it is always within the plot window
    event_buffer = buffer for the lines, so that they always extend slightly past the plot limits (to look nice)
    hard_min, hard_max = values if we always want to have things at least that low/high.
    """
    valid = np.array(data[np.isfinite(data)]) #making sure we have data that is nice
    if len(valid) == 0:
        return (0, 1)
    
    min_val = np.nanmin(valid) * buffer[0]
    max_val = np.nanmax(valid) * buffer[1]
    line_min = np.nanmin(valid) * event_buffer[0]
    line_max = np.nanmax(valid) * event_buffer[1]
    
    if hard_min is not None:
        min_val = np.nanmin([min_val, hard_min]) 
    if hard_max is not None:
        max_val = np.nanmax([max_val, hard_max])
    
    widget.plotItem.vb.setLimits(yMin=min_val, yMax=max_val)
    return (line_min, line_max)

def display_temp(self):
    """Configure Temperature plot appearance and range."""
    data = self.goes['Temp'].iloc[-65:] #this is longer since we now have 30 second cadence data!
    self.line_min_temp, self.line_max_temp = self._set_y_limits(
        self.tempgraph, data, buffer=(0.9, 1.1), event_buffer=(0.8, 1.2), hard_min=2, hard_max=18
    )
    self._configure_axes(self.tempgraph)

def display_em(self):
    """Similarly, configure EM plot appearance and range."""
    data = self.goes['emission measure'].iloc[-65:]
    self.line_min_em, self.line_max_em = self._set_y_limits(
        self.emgraph, data, buffer=(1.0, 1.0), event_buffer=(0.8, 1.2)
    )
    self._configure_axes(self.emgraph)

def display_eve0(self):
    """And also configure EVE plot appearance and range."""
    data = self.eve_current['ESP_0_7_COUNTS']
    self.line_min_eve0, self.line_max_eve0 = self._set_y_limits(
        self.evegraph0, data, buffer=(0.6, 1.4), event_buffer=(0.5, 1.5)
    )
    self._configure_axes(self.evegraph0, y_log=self._logy)

def xlims(self):
    """ Control the x-limits for plots. """
    # self.graphWidget.plotItem.setXRange(pd.Timestamp(self.goes.iloc[-30]['time_tag']).timestamp(), pd.Timestamp(self._get_datetime_now()).timestamp())
    _now = self._get_datetime_now()
    xmin = pd.Timestamp(_now-timedelta(minutes=30)).timestamp()
    xmax = pd.Timestamp(_now).timestamp()
    _plot_offest = -60 #seconds, for some reason the plot extends by about this much :(
    self.graphWidget.plotItem.setXRange(xmin, xmax + _plot_offest)
    self.tempgraph.plotItem.setXRange(xmin, xmax + _plot_offest)
    self.emgraph.plotItem.setXRange(xmin, xmax + _plot_offest)
    if not self.no_eve:
        self.evegraph0.plotItem.setXRange(xmin, xmax + _plot_offest)

############## END OF WHAT IM HAPPY WITH FOR DISPLAY ############################



###### CHAT SUGGESTIONS!!!!!
### chat suggestions for handling GOES display:

#fist, cleaning up computing the goes Y limits
def _compute_goes_ylimits(self, min_array, max_array, margin=0.25):
    finite_min = np.nanmin(min_array[np.isfinite(min_array)])
    finite_max = np.nanmax(max_array[np.isfinite(max_array)])

    if self._logy:
        lower = max(self._lowest_yrange, np.log10(finite_min) - margin)
        upper = min(np.log10(finite_max) + margin, self._highest_yrange)
    else:
        lower = max(10**self._lowest_yrange, finite_min - margin)
        upper = min(finite_max + margin, 10**self._highest_yrange)

    return lower, upper





##### doing updates!!!
# Update main data plots
self.plots['XRSA'].setData(self.time_tags, new_xrsa_data)
self.plots['Temp'].setData(self.time_tags, new_temp_data)
self.plots['EM'].setData(self.time_tags, new_em_data)

# Update event lines
new_fai_time = pd.Timestamp(new_goes_time).timestamp()
self.plots['FAI'].setData([new_fai_time]*2, [1e-9, 1e-3])
self.plots['Data Trigger'].setData([new_time]*2, [1e-9, 1e-3])
