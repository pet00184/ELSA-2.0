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
        self.load_data(reload=False, initial=True)
        if self.no_eve==False:
            self.load_eve_data(reload=False)
        
        #initializing the plot styles and layout: 
        self._setup_plot_layout()
        self._setup_plot_styles(self.graphWidget, 'W m<sup>-2</sup>', 'GOES XRS')
        self._setup_plot_styles(self.tempgraph, 'MK', 'Temperature')
        self._setup_plot_styles(self.emgraph, 'cm<sup>-3</sup>', 'Emission Measure')
        self._setup_plot_styles(self.evegraph0, 'Raw Counts', 'EVE ESP 0-7nm')

        #initializing the display for all plots (this is mainly fixing the axis ranges)
        self._min_arr, self._max_arr = "xrsa", "xrsb" #initializing that we want to focus on both XRSA and XRSB in the plot
        self._logy = True #initializing that we want to start in log scale
        self.display_allgoes_plots()
        self.display_eve_plots()
            
        #PLOTTING GOES, EVE and all the event lines
        self._setup_GOES_plotdata()
        self._setup_EVE_plotdata()
            
        #Defining the flare trigger
        self._setup_trigger(test_trigger)
        
        #updating data!!
        self._start_timer()

    ################################ MAIN UPDATING FUNCTION #######################################
    def _update(self):
        #do EVE data loading if there is EVE
        if self.no_eve==False:
            self.load_eve_data()
            self.check_for_new_eve_data()
            if self.new_eve_data:
                self.update_eve_plots()
        # if self.no_eve==True:
        #     self.no_eve_plot_update()
        #load in the GOES data and see if there is new data to plot
        self.load_data()
        self.check_for_new_data()
        if self.new_data:
            #update the GOES plots and FAI
            self.update_goes_plots()
            if self.flare_happening: 
                self.check_for_flare_end()
            if self._flare_prediction_state == "searching":
                self.check_for_trigger()
            elif self._flare_prediction_state == "triggered":
                self.trigger_sound_effect.play()
            elif self._flare_prediction_state == "launched":
                self.check_for_post_launch()
            elif self._flare_prediction_state == "post-launch":
                self.check_for_search_again()
            #update the trigger and launch plots once we checked through the states
            self.update_all_line_plots()
            # self.update_launch_plot()
            # self.update_eve_launch_plots()
            # self.update_temp_em_launch_plots()
            #save the data
            self.save_data()
        #update all the xlimits once again... do I need this? should it be with the plot updates? 
        self.xlims()
        self.update()

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

        # Other event lines
        for name, color in events:
            if name not in plots:
                plots[name] = []
            plot_item = self.plot_data(plotwidget, [time_start]*2, y_range, color, name, symbol=False)
            plot_item.setAlpha(0, False)
            plots[name].append(plot_item)

        # FAI line
        if fai_loc is not None and fai_time is not None:
            if 'FAI' not in plots:
                plots['FAI'] = []
            fai_plot = self.plot_data(plotwidget, [fai_time]*2, y_range, 'pink', 'FAI', symbol=False)
            fai_plot.setAlpha(1 if fai_loc > 0 else 0, False)
            plots['FAI'].append(fai_plot)

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

    ########################## DISPLAY FUNCTIONS #########################################################################

    def display_allgoes_plots(self):
            """This is the function that goes through and does the display for all the GOES plots (temp, em, GOES). Call this every time we have a new updated data point."""
            self.xlims()
            self.display_goes()
            self.display_temp()
            self.display_em()

    def display_eve_plots(self):
            """This will display the eve plot nicely. Call this every time we have a new EVE data point."""
            self.display_eve0()

    def _log_data(self, array):
        """ Check if the data is to be logged with `self._logy`."""
        if self._logy:
            log = np.log10(array)
            return log
        return array
    
    def _configure_axes(self, widget, y_log=False):
        """Uniformly configure axes appearance and log mode. This is the same fof all plots."""
        widget.showAxis('top')
        widget.getAxis('top').setStyle(showValues=False)
        widget.getAxis('top').setGrid(False)
        
        widget.showAxis('right')
        widget.getAxis('right').setGrid(False)
        widget.getAxis('right').enableAutoSIPrefix(enable=False)
        
        widget.setLogMode(x=False, y=y_log)
        widget.plot()

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

    def _set_y_limits(self, widget, data, buffer=(0.9, 1.1), event_buffer=(0.8, 1.2), hard_min=None, hard_max=None):
        """Compute min/max and set limits for a plot widget. This is used for everything but GOES, that has its own special stuff to work with the fancier plotting.
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

    def display_goes(self):
        """ Displays the GOES plot, with twin axes that show intermediate GOES-class values."""

        ## set the y-limits for the axes
        self._goes_ylims()
        #do all the ticks and fancy goes axis plotting
        self._display_goes_ticksnclasses
        # configure the axes
        self._configure_axes(self.graphWidget, y_log=self._logy)

    
    def _display_goes_ticksnclasses(self):
        """Deals with the GOES ticks, specifically which will be shown. It also deals with the plotting of the right axis (showing the GOES classes, and deciding
        which of the intermediate classes we are interested in plotting)."""

        ## setting up the labels for the GOES class axis
        log_value = np.arange(-10,-1) # get the letter class log-values
        value = 10**(log_value.astype(float)) # get the letter class values
        
        intermediate_classes = [1,2,3,4,5,6,7,8,9]
        num_of_int = [value[None,:] for _ in range(len(intermediate_classes))]
        value_ints = (np.vstack(num_of_int).T*np.array(intermediate_classes)).flatten() # now go letter class, half up, next letter class; e.g., A, A5, B, B5, etc.
        log_value_ints = self._log_data(value_ints)

        def _goes_strings(cls, arng, append=""):
            """ GenerateGOES class strings."""
            return [cls+str(v)+append for v in arng]
        
        goes_labels_ints = _goes_strings("A0.0", arng=intermediate_classes)+\
                            _goes_strings("A0.", arng=intermediate_classes)+\
                            _goes_strings("A", arng=intermediate_classes)+\
                            _goes_strings("B", arng=intermediate_classes)+\
                            _goes_strings("C", arng=intermediate_classes)+\
                            _goes_strings("M", arng=intermediate_classes)+\
                            _goes_strings("X", arng=intermediate_classes)+\
                            _goes_strings("X", arng=intermediate_classes, append="0")+\
                            _goes_strings("X", arng=intermediate_classes, append="00")
        
        def ticks_display():
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
    
        keep_intermediate_classes = ticks_display()

        def _keep_goes_intermediate(intermediate_classes, classes_to_keep):
            """ Work out which intermediate GOES class to plot the tick labels for. """
            return [(np.array(classes_to_keep)-1)+i*len(intermediate_classes) for i in range(9)]

        goes_labels_ints_keep = _keep_goes_intermediate(intermediate_classes=intermediate_classes, classes_to_keep=keep_intermediate_classes)
        goes_value_ints_keep = log_value_ints[goes_labels_ints_keep]

        if self._logy:
            self.graphWidget.getAxis('right').setTicks([[(v, str(s)) if (v in goes_value_ints_keep) else (v,"") for v,s in zip(log_value_ints,goes_labels_ints)]])
            self.graphWidget.getAxis('left').setTicks([[(v, f"{s:0.0e}") if (v in goes_value_ints_keep) else (v,"") for v,s in zip(log_value_ints,value_ints)]])
        else: 
            self.graphWidget.getAxis('right').setTicks([[(v, str(s)) if (v in goes_value_ints_keep) else (v,"") for v,s in zip(value_ints,goes_labels_ints)]])
            self.graphWidget.getAxis('left').setTicks([[(v, f"{s:0.0e}") if (v in goes_value_ints_keep) else (v,"") for v,s in zip(value_ints,value_ints)]])

    def _goes_ylims(self):
        """ 
        The ylims are:
            ymin = A-class or half an order of magnitude below the min. of `self._min_arr`.
            ymax = X10-class or half an order of magnitude above the max. of `self._max_arr`.

        The ylims are, by DEFAULT:
            ymin = A-class or half an order of magnitude below the min. of XRSA.
            ymax = X10-class or half an order of magnitude above the max. of XRSB.
        """

        self._lowest_yrange, self._highest_yrange = -8*1.02, -3*0.96 

        #making sure we are only getting XRSA and/or XRSB
        _supported_arrays = ["xrsa", "xrsb"]
        if (self._min_arr not in _supported_arrays) or (self._max_arr not in _supported_arrays):
            print(f"self._min_arr={self._min_arr} or self._max_arr={self._max_arr} not in _supported_arrays={_supported_arrays}.")
            return
        
        # We want to be taking from the new data, which is only the last 60 points.
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

    ########################################## FLARE PREDICTION STATE FUNCTIONS ###############################################################  
    def flare_prediction_state(self, state):
        self._flare_prediction_state = state
        self.value_changed_signal_status.emit()

    def change_to_searching_state(self):
        self.flare_prediction_state("searching")
        self.flare_happening = False
        
    def change_to_triggered_state(self):
        self.flare_prediction_state("triggered")
        self.flare_happening = True
        self.flare_summary_index += 1
        
    def change_to_launched_state(self):
        self.flare_prediction_state("launched")
        
    def change_to_post_launch_state(self):
        self.flare_prediction_state("post-launch")

    ############################################ NEW DATA LOADING AND PROCESSING FUNCTIONS ###########################################################################

    ## loading new data
    def load_data(self, reload=True, initial=False):
        """Loads in the GOES data from the source. If it is the first load, you define the main GOES DF as the current load in and do initial parameter and FAI 
        calculations. This is also where the latest data time is saved, as well as the realtime of the load in."""
        # get the current data from the GOES loading process (this will give first 65 files if initial load, otherwise just 5)
        self.goes_current = self.XRS_data(initial=initial)

        self.current_time = list(self.goes_current["time_tag"])[-1]
        self.current_realtime = self._get_datetime_now()

        if not reload:
            # First-time setup
            self.goes = self.goes_current
            self.calculate_param_arrays(0, new=False)
            self.check_for_FAI(0, new=False)

    def load_eve_data(self, reload=True):
        """This loads in the EVE data. If it is the first load, the main EVE DF is defined as the current load in."""
        self.eve_current = self.EVE_data()
        if not reload:
            self.eve = self.eve_current

    ## checking for new data from the loaded stuff
    def check_for_new_data(self):
        """ Check for new data and add to what is plotted. """
        self.new_data = False
        # get indices for any data that has a newer time than the newest plotted
        new_times = self.goes_current.iloc[:]['time_tag']>list(self.goes['time_tag'])[-1]
        # if there are >0 new data-points then append them to the plotting data
        if len(self.goes_current[new_times]['time_tag']) > 0: 
            added_points = len(self.goes_current[new_times]['time_tag'])
            self.goes = self.goes._append(self.goes_current[new_times], ignore_index=True)
            self.calculate_param_arrays(added_points, new=True)
            self.check_for_FAI(added_points, new=True)
            self.new_data = True

            # make sure the y-limits change with the plot if needed and alert that new data is added
            #self.display_allgoes_plots() ### TAKING THIS OUT HERE BECAUSE WE SHOULD ONLY UPDATE THE DISPLAY WHEN WE UPDATE THE PLOT.
            self.value_changed_new_xrsb.emit()

    def check_for_new_eve_data(self):
        """ Checking for new EVE data. This will update more quickly, about once every 10 seconds!"""
        self.new_eve_data = False
        new_times = self.eve_current.iloc[:]['UTC_TIME'] > list(self.eve['UTC_TIME'])[-1]
        
        if len(self.eve_current[new_times]['UTC_TIME']) > 0:
            added_points = len(self.eve_current[new_times]['UTC_TIME'])
            self.eve = self.eve._append(self.eve_current[new_times], ignore_index=True)
            self.new_eve_data=True

    ## calculating GOES parameters and checking for the FAI
    def calculate_param_arrays(self, added_points, new=False):
        ''' Calculates temperature etc. and appends a new column to the data (or just last thing. work in progress)
        '''
        differences_to_calculate = [3, 5]
        if not new:
            for diff in differences_to_calculate:
                steps = diff*2 #to deal with the 30 second cadence instead of 1 minute!!
                for xrs in ['xrsa', 'xrsb']:
                    xrsdiff = np.array(self.goes[xrs])
                    xrsdiff = xrsdiff[steps:] - xrsdiff[:-steps]
                    xrsdiff_final = np.concatenate([np.full(steps, np.nan), xrsdiff]) #appending correct # of 0's to front
                    self.goes[f'{diff}min{xrs}diff'] = xrsdiff_final
            #calculating em and temp here:
            em, temp = emission_measure.compute_goes_emission_measure(self.goes['xrsa'], self.goes['xrsb'], self.goes['satellite'])
            self.goes['emission measure'] = em #(em := emission_measure.compute_goes_emission_measure(self.goes))
            self.goes['Temp'] = temp
            #calculatign FAI param arrays here
            for diff in differences_to_calculate:
                em, temp = emission_measure.compute_goes_emission_measure(self.goes[f'{diff}minxrsadiff'], self.goes[f'{diff}minxrsbdiff'], self.goes['satellite'])
                self.goes[f'{diff}min emission measure'] = em
                self.goes[f'{diff}min Temp'] = temp
        if new:
            for i in range(added_points):
                new_point = -(added_points-i)
                #calculating 3-min difference is here:
                for diff in differences_to_calculate:
                    steps = diff*2 # for switching from 1 minute cadence to 30 second cadence
                    for xrs in ['xrsa', 'xrsb']:
                        self.goes.iloc[new_point, self.goes.columns.get_loc(f'{diff}min{xrs}diff')] = self.goes.iloc[new_point, self.goes.columns.get_loc(xrs)] - self.goes.iloc[new_point - steps, self.goes.columns.get_loc(xrs)]
                #calculating em and temp is here:
                em, temp = emission_measure.compute_goes_emission_measure(self.goes['xrsa'].iloc[new_point], self.goes['xrsb'].iloc[new_point], self.goes['satellite'].iloc[new_point])
                em_loc = self.goes.columns.get_loc('emission measure')
                temp_loc = self.goes.columns.get_loc('Temp')
                self.goes.iloc[new_point, em_loc] = em[0]
                self.goes.iloc[new_point, temp_loc] = temp[0]
                #calculating FAI param arrays here:
                for diff in differences_to_calculate:
                    em, temp = emission_measure.compute_goes_emission_measure(self.goes[f'{diff}minxrsadiff'].iloc[new_point], self.goes[f'{diff}minxrsbdiff'].iloc[new_point], self.goes['satellite'].iloc[new_point])
                    em_loc = self.goes.columns.get_loc(f'{diff}min emission measure')
                    temp_loc = self.goes.columns.get_loc(f'{diff}min Temp')
                    self.goes.iloc[new_point, em_loc] = em[0]
                    self.goes.iloc[new_point, temp_loc] = temp[0]

    def check_for_FAI(self, added_points, new=True):
        ''' For the initial load we want to see if there was any FAI for the 
        previous 30 min plotted, and plot that line if there was one (at the latest point.) Then, we want to have 
        the FAI get checked on just the last datapoint, and have it change to that if it is true. if not, stay at the 
        other most recent point.
        '''
        #self.FAI_loc = None
        if not new:
            potential_FAIs = np.where((self.goes['5min emission measure'] > .05e49) & (self.goes['5min Temp'] > 6))[0]
            if len(potential_FAIs) > 0:
                self.FAI_loc = potential_FAIs[-1]
                self.fai_summary_index += 1
                self.fai_summary.loc[self.fai_summary_index, 'Flare_Index'] = self.flare_summary_index
                self.fai_summary.loc[self.fai_summary_index, 'FAI_Time'] = self.goes['time_tag'].iloc[self.FAI_loc]
        if new:
            for i in range(added_points):
                new_point = -(added_points-i)
                new_FAI = (self.goes['5min emission measure'].iloc[new_point] > .05e49) & (self.goes['5min Temp'].iloc[new_point] > 6)
                if new_FAI:
                    self.FAI_loc = np.where(self.goes['time_tag'] == self.goes['time_tag'].iloc[new_point])[0][0]
                    self.fai_summary_index += 1
                    self.fai_summary.loc[self.fai_summary_index, 'Flare_Index'] = self.flare_summary_index
                    self.fai_summary.loc[self.fai_summary_index, 'FAI_Time'] = self.goes['time_tag'].iloc[self.FAI_loc]

    ################################################ SEARCHING TO TRIGGER UPDATE FUNCTIONS ############################################
    def update_flare_alerts(self):  
        """ Function to update the alerts and emit a signal. """
        for a in self.flare_alert_names:
            self.flare_alerts.at['states', a] = self.flare_alert_map[a](goes_data=self.goes)  
        self.value_changed_alerts.emit()
    
    def check_for_trigger(self):
        self.update_flare_alerts()
        if np.all(self.flare_alerts.loc['states']):
            self.change_to_triggered_state()
            self.flare_summary.loc[self.flare_summary_index, 'Trigger'] = self.current_time
            self.flare_summary.loc[self.flare_summary_index, 'Realtime Trigger'] = self.current_realtime
            print(f'FLARE TRIGGERED on {self.current_time} flux, at {self.current_realtime} UTC.')
            self.trigger_sound_effect.play()
        else:
            if self.print_updates: print('Still searching for flare')
    
    ############################################## COUNTDOWN UPDATE FUNCTIONS ######################################################
    def check_for_flare_end(self):
        """Checks to see if the flare end condition is met. If this happens, it puts out a warning to not launch. If we are triggered but not launched, it 
        changes back to a searching state. If this happens, the user should stop the countdown."""
        if fc.flare_end_condition(goes_data=self.goes):
            self.flare_summary.loc[self.flare_summary_index, 'Flare End'] = self.current_time
            if self._flare_prediction_state == "triggered":
                self.change_to_searching_state()
                print(f'Flare ended at {self.current_time}. DO NOT LAUNCH! Searching for another flare.')
            elif self._flare_prediction_state == "launched":
                self.flare_happening = False
                print(f'Flare ended during observation at {self.current_time}.')

    def _button_press_save_countdown_time(self):
        '''Button used to save when the launch countdown is started. We may want to build upon this and have
        a countdown window begin in the GUI itself.
        '''
        self.flare_summary.loc[self.flare_summary_index, 'Countdown Initiated'] = self.current_realtime

    def save_hold_time(self):
        ''' Saves the time the hold launch button was pressed, if pressed.
        '''
        self.flare_summary.loc[self.flare_summary_index, 'Hold'] = self.current_realtime

    ########################################### LAUNCHING UPDATE FUNCTIONS #########################################################
    def _button_press_launch(self):
        ''' Button used for changing to launch stage. Used to be to change to pre-launch stage, but now we are 
        surpassing that and going straight to launched state!
        '''
        if not hasattr(self,"coming_launch_time"):
            self.coming_launch_time = self.current_realtime #+timedelta(minutes=self.PRE_LAUNCH_WINDOW) #changed from get current time until we get the realtime vs. current_realtime all sorted
            self.coming_launch_time_hic = self.coming_launch_time + timedelta(minutes=1)
        self.change_to_launched_state()
        self.save_observation_times()

    def save_observation_times(self):
        ''' Saves the time of launch, as well as the FOXSI and Hi-C observation start and end times, which are based off of
        the launch time.
        '''
        self.flare_summary.loc[self.flare_summary_index, 'Launch'] = self.current_realtime
        self.flare_summary.loc[self.flare_summary_index, 'HiC Launch'] = self.current_realtime + pd.Timedelta(1, unit='minutes')
        foxsi_obs_start = self.flare_summary['Launch'].iloc[-1] + pd.Timedelta(self.LAUNCH_TO_FOXSI_OBS_START, unit='minutes')
        foxsi_obs_end = self.flare_summary['Launch'].iloc[-1] + pd.Timedelta(self.LAUNCH_TO_FOXSI_OBS_END, unit='minutes')
        hic_obs_start = self.flare_summary['Launch'].iloc[-1] + pd.Timedelta(self.LAUNCH_TO_HIC_OBS_START, unit='minutes')
        hic_obs_end = self.flare_summary['Launch'].iloc[-1] + pd.Timedelta(self.LAUNCH_TO_HIC_OBS_END, unit='minutes')
        self.flare_summary.loc[self.flare_summary_index, 'FOXSI Obs Start'] = foxsi_obs_start
        self.flare_summary.loc[self.flare_summary_index, 'FOXSI Obs End'] = foxsi_obs_end
        self.flare_summary.loc[self.flare_summary_index, 'HiC Obs Start'] = hic_obs_start
        self.flare_summary.loc[self.flare_summary_index, 'HiC Obs End'] = hic_obs_end
        self._launched = None

    ################################# POST LAUNCH FUNCTIONS ################################################################
    def check_for_post_launch(self):
        if self.current_realtime >= self.flare_summary['HiC Obs End'].iloc[-1] and self._flare_prediction_state == "launched":
            self.change_to_post_launch_state()
            print('Entering post-observation deadtime.')
            
    def check_for_search_again(self):
        if self.current_realtime >= self.flare_summary['HiC Obs End'].iloc[-1] + pd.Timedelta(self.DEADTIME, unit='minutes'): 
            if self.flare_happening:
                self.flare_summary.loc[self.flare_summary_index, 'Flare End'] = self.current_time
                print(f'Flare end condition not met within post-launch window. Setting flare end time to most recent data: {self.current_time}.')
            self.change_to_searching_state()
            print(f'Ready to look for another flare at {self.current_realtime}!')
        elif pd.isnull(self.flare_summary['HiC Obs End'].iloc[-1]) and self.current_realtime == self.flare_summary['Realtime Trigger'].iloc[-1] + pd.Timedelta(self.DEADTIME, unit='minutes'):
            self.change_to_searching_state()
            print(f'Ready to look for another flare at {self.current_realtime}! {self.flare_happening}')

    ################################ PLOTTING UPDATE FUNCTIONS #############################################################
    def update_goes_plots(self):
        """Updates the GOES, Temp and EM plots. This is called every time new GOES data is downloaded."""
        #get the most recent time tags for GOES
        recent_goes = self.goes.iloc[-60:] if self.goes.shape[0] > 60 else self.goes
        self.goes_time_tags = [pd.Timestamp(t).timestamp() for t in recent_goes['time_tag']]

        # Loop through updating all the GOES plots from the plot configs
        for name, config in self.goes_plot_configs.items():
            widget = config['widget']
            logy = config['logy']
            for key, (color, _) in config['data'].items():
                if key not in self.plots:
                    continue  # just making sure that all the plots are made- this should never be an issue.
                # Get updated y-data
                ydata = np.array(recent_goes[key]) if key in recent_goes else None
                if ydata is None:
                    continue
                # Log if needed
                ydata = self._log_data(ydata) if logy else ydata
                # Update the plot!
                self.plots[key].setData(self.goes_time_tags, ydata)
        #update the displays nicely
        self.display_allgoes_plots()
        self.xlims()

    def update_eve_plots(self):
        """Updates the EVE irradiance plot, following the same logic as the GOES plots (in case we ever add more EVE).
        We are keeping this separate because I will be updating GOES and EVE independently as they update at different rates."""
        self.eve_time_tags = [pd.Timestamp(str(date)).timestamp() for date in self.eve['UTC_TIME']]

        config = self.eve_plot_configs['EVE0']
        logy = config.get('logy', False)

        for key, (color, _) in config['data'].items():
            ydata = np.array(self.eve[key])
            if logy:
                ydata = self._log_data(ydata)

            self.plots[key].setData(self.eve_time_tags, ydata)
        # upate the EVE display and the x axes for everything so they stay the same
        self.display_eve_plots()
        self.xlims()

    ## trigger plot updates
    def update_line_plots(self, dataconfig_dict, plotlist):
        """Update the trigger values for the 'Data Trigger' and 'Realtime Trigger' plots"""
    
        # Get last trigger times and whether they are in the last 60 goes time tags
        last_trigger = self.flare_summary['Trigger'].iloc[-1]
        last_realtime = self.flare_summary['Realtime Trigger'].iloc[-1]
        last_fai = self.goes['time_tag'].iloc[self.FAI_loc]

        in_window_trigger = last_trigger in list(self.goes['time_tag'].iloc[-60:])
        in_window_realtime = last_realtime in list(self.goes['time_tag'].iloc[-60:])
        in_window_fai = last_fai in list(self.goes['time_tag'].iloc[-60:])

        trigger_time, trigger_alpha = (
            pd.Timestamp(last_trigger).timestamp() if in_window_trigger else self.goes_time_tags[0],
            1 if in_window_trigger else 0
        )
        realtime_time, realtime_alpha = (
            pd.Timestamp(last_realtime).timestamp() if in_window_realtime else self.goes_time_tags[0],
            1 if in_window_realtime else 0
        )
        fai_time, fai_alpha = (
            pd.Timestamp(last_fai).timestamp() if in_window_fai else self.goes_time_tags[0],
            1 if in_window_fai else 0
        )

        # Map plot names to their times & alpha
        event_times = {
            'Data Trigger': (trigger_time, trigger_alpha),
            'Actual time of Trigger': (realtime_time, realtime_alpha),
            'FAI': (fai_time, fai_alpha)
        }

        # Get the dynamic config order from the dictionary keys (GOES, Temp, EM)
        config_order = list(dataconfig_dict.keys())

        # Loop through plots
        for line_name, (t, a) in event_times.items():
            for i, plot_item in enumerate(plotlist[line_name]):
                config_name = config_order[i]
                y_range = dataconfig_dict[config_name]['event_range']
                plot_item.setData([t]*2, y_range)
                plot_item.setAlpha(a, False)

    def update_all_line_plots(self):
        """Updates the trigger plots on GOES and EVE plots"""
        #GOES
        self.update_line_plots(self.goes_plot_configs, self.plots)
        #EVE
        self.update_line_plots(self.eve_plot_configs, self.eve_plots)
        self.display_allgoes_plots()
        self.display_eve_plots()
        self.xlims()



    ############################################ MISCELLANEOUS FUNCTIONS #####################################################
    def _get_datetime_now(self):
        """ Always return the current UTC time. """
        return datetime.now(timezone.utc)
    
    def save_data(self):
        """Saves all GOES and EVE data from the time the GUI was open to .csv files. Also saves times of all FAI's, triggers, holds and launches."""
        self.flare_summary.to_csv(os.path.join(PACKAGE_DIR, "SessionSummaries", self.foldername, "timetag_summary.csv"))
        self.goes.to_csv(os.path.join(PACKAGE_DIR, "SessionSummaries", self.foldername, "GOES.csv"))
        self.fai_summary.to_csv(os.path.join(PACKAGE_DIR, "SessionSummaries", self.foldername, 'fai_summary.csv'))
        if not self.no_eve:
            self.eve.to_csv(os.path.join(PACKAGE_DIR, "SessionSummaries", self.foldername, "EVE.csv"))



###### CHAT SUGGESTIONS!!!!!

# def update_all_event_lines(self):
#     """
#     Update all event lines (triggers, FAI, launches, etc.) for both GOES and EVE.
#     """
#     for instrument, (plot_configs, plotlist, flare_data, time_tags, FAI_loc) in {
#         'GOES': (self.goes_plot_configs, self.plots, self.flare_summary, self.goes['time_tag'], self.FAI_loc),
#         'EVE': (self.eve_plot_configs, self.eve_plots, self.eve_flare_summary, self.eve['time_tag'], getattr(self, 'FAI_loc_eve', -1))
#     }.items():

#         # --- Triggers & FAI ---
#         # Get last trigger times and check if they're in the last 60 time tags
#         last_trigger = flare_data['Trigger'].iloc[-1]
#         last_realtime = flare_data['Realtime Trigger'].iloc[-1]
#         last_fai = time_tags.iloc[FAI_loc] if FAI_loc >= 0 else time_tags.iloc[0]

#         in_window_trigger = last_trigger in list(time_tags.iloc[-60:])
#         in_window_realtime = last_realtime in list(time_tags.iloc[-60:])
#         in_window_fai = last_fai in list(time_tags.iloc[-60:])

#         trigger_time, trigger_alpha = (
#             pd.Timestamp(last_trigger).timestamp() if in_window_trigger else time_tags.iloc[0],
#             1 if in_window_trigger else 0
#         )
#         realtime_time, realtime_alpha = (
#             pd.Timestamp(last_realtime).timestamp() if in_window_realtime else time_tags.iloc[0],
#             1 if in_window_realtime else 0
#         )
#         fai_time, fai_alpha = (
#             pd.Timestamp(last_fai).timestamp() if in_window_fai else time_tags.iloc[0],
#             1 if in_window_fai else 0
#         )

#         # --- Launch events ---
#         foxsi_time, foxsi_alpha = getattr(self, 'coming_launch_time', {}).get('FOXSI', 0), 1 if getattr(self, '_launched', {}).get('FOXSI', False) else 0
#         hic_time, hic_alpha = getattr(self, 'coming_launch_time', {}).get('HiC', 0), 1 if getattr(self, '_launched', {}).get('HiC', False) else 0

#         # Combine all into event_times
#         event_times = {
#             'Data Trigger': (trigger_time, trigger_alpha),
#             'Actual time of Trigger': (realtime_time, realtime_alpha),
#             'FAI': (fai_time, fai_alpha),
#             'FOXSI Launch': (foxsi_time, foxsi_alpha),
#             'HIC Launch': (hic_time, hic_alpha)
#         }

#         # Update all event lines using the universal method
#         self.update_event_lines(plot_configs, plotlist, event_times)

