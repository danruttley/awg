import logging
import sys
import inspect
import numpy as np
from copy import copy, deepcopy

from .phase_minimiser import phase_minimise

from os import path
sys.path.append(path.dirname(path.dirname(path.abspath(__file__))))

max_tone_num = 100

shared_segment_params = ['duration_ms','phase_behaviour'] # parameters that have to be shared between actions in the same segment

class ActionContainer():
    """Container for a given single-channel AWG segment containing both phase 
    (frequency) and amplitude information.
    
    The class is designed to handle multiple frequency tones. All kwargs passed
    to the `ActionContainer` should be as lists, even if only one tone is 
    requested (this is autohandled if using the GUI).
    
    The class handles data for a single output channel. If using multiple
    channels data will need to be interleaved before being sent to the AWG.

    ...

    Attributes
    ----------
    action_params : dict
        Parameters defining the phase (frequency) and amplitude behaviour.
        The dictionary should contain two dictionaries with keys 'freq' and 
        'amp' which contains the kwarg args for the frequency and amplitude 
        functions, as well as the function name with the key 'function'. The
        dictionary should also contain the entry 'duration_ms' which defines
        the segment length.
    card_settings : dict
        Dictionary containing the global AWG card parameters (such as the 
        sample rate).
    time : array
        `numpy` array containing the timesteps for the action, in seconds.
    data : array
        `numpy` array containing the data to send to the AWG. This is only
        calculated when expliticitly requested with the `calculate` method; 
        until this point it will be an empty array with the same size at the 
        `time` attribute.
    phase_behaviour : {'optimise','continue','manual'}
        Defines the phase behaviour that this action will take to set the 
        phases of the tones in the action.
        
        Note that 'continue' will only be used if the GUI option to enforce 
        continuity between segments is set.
        
        'optimise': the phases of the tones will be optimise to minimise the 
                    crest factor of the tone. This will almost certainly cause 
                    a phase jump from the segment before.
        'continue': the end phases from the previous segment will be used as 
                    this segment's starting phases. If more tones are present 
                    in this segment than before, these phases will be optimised
                    but the others will be left unchanged. If fewer tones are 
                    present in this segment than before, the excess phases will
                    be discarded.
                    
                    This flag will cause the MainWindow object to pass through 
                    the end phases from the previous segment when it is 
                    calculated. This will prevent phase jumps (definitely in 
                    cases where the number of tones is conserved, potentially 
                    not in cases when the number of tones vary).
        'manual':   The phases manually set when generating the segment will be 
                    used. This could cause a phase jump if the phases are not 
                    correctly set.
    needs_to_calculate : bool
        Boolean that contains whether the class has recieved an update to one
        of its parameters that means that data needs to recalculated when 
        called.
    needs_to_transfer : bool
        Boolean that contains whether this action needs to be  copied to the 
        AWG card. It is set to True when data is calculated and should only be
        set to False by the `AWG` class when it has transfered the data. This 
        allows the `AWG` class to keep track of whether it needs to transfer 
        new data to the card or not.
        
        When data is changed in the GUI, all segments will retransfer incase 
        their order has changed. This flag prevents that needing to take place 
        if a change is requested from PyDex.
    rearr : bool
        Boolean used by the `MainWindow` class to track whether this action 
        is used for rearrangement. This has to be toggled externally, no method
        in this class will toggle this boolean.
    amp_adjuster : AmpAdjuster2D
        The AmpAdjuster for this action_container. This is a shared object 
        between all ActionContainers of the same channel. It converts the 
        desired optical power into mV to be sent to the AWG.
        
        All data to be sent to the card should be passed through to this 
        AmpAdjuster even if amplitude adjusting behaviour is not desired; the 
        AmpAdjuster will handle this and return the same amplitude for all 
        frequencies (scaled by its `non_adjusted_amp_mV` attribute). 
        
    """
    
    def __init__(self,action_params,card_settings,amp_adjuster):
        for shared_segment_param in shared_segment_params:
            setattr(self,shared_segment_param,action_params[shared_segment_param])
        
        self.freq_params = action_params['freq']
        self.freq_function_name = self.freq_params.pop('function')
        self.amp_params = action_params['amp']
        self.amp_function_name = self.amp_params.pop('function')

        self.card_settings = card_settings

        self.freq_function = eval('self.freq_{}'.format(self.freq_function_name))
        self.amp_function = eval('self.amp_{}'.format(self.amp_function_name))
        
        self.equalise_param_lengths()

        self.calculate_time()
        
        self.amp_adjuster = amp_adjuster
        
        self.needs_to_calculate = True
        self.needs_to_transfer = True
        self.rearr = False
        
    def get_action_params(self):
        """Returns a complete `action_params` dict that could be used to 
        recreate this action container.
        
        The dictionary is a complete copy to ensure that the attributes of 
        this class are not editied (note this may not be needed).
        
        Returns
        -------
        dict
            action_params dict equal to the one used to create this 
            `ActionContainer object.
            
        """
        action_params = {}
        for shared_segment_param in shared_segment_params:
            action_params[shared_segment_param] = getattr(self,shared_segment_param)
        
        freq_params = {'function' : self.freq_function_name}
        amp_params = {'function' : self.amp_function_name}
        
        freq_params.update(self.freq_params)
        amp_params.update(self.amp_params)
        
        action_params['freq'] = freq_params
        action_params['amp'] = amp_params
        
        return deepcopy(action_params)

    def update_param(self,target_function,param,value):
        """Updates the relvant function dictionary with a new parameter value.
        
        Recalculation of the action data is NOT performed to allow for fast 
        autoupdating of parameters when e.g. preventing frequency jumps in the
        GUI. However the boolean attribute `needs_to_calculate` is set to
        True which will ensure recalculation is performed when the 
        `calculate` method is called.
        

        Parameters
        ----------
        target_function : {'freq', 'amp'}
            The target function to update the parameter of (either the 
            frequency or amplitude function).
        param : str
            The function kwarg to change.
        value : float
            The value to change the arguement to.

        Raises
        ------
        NameError
            Raised if an invalid arguement for the given `target_function` is 
            specfied.

        Returns
        -------
        None.

        """
        if target_function == 'freq':
            if param in inspect.getfullargspec(self.freq_function)[0]:
                if value != self.freq_params[param]:
                    self.freq_params[param] = value
                    self.equalise_param_lengths()
                    self.needs_to_calculate = True
            else:
                raise NameError('{} is not a parameter for freq_{} function'.format(param,self.freq_function_name))
        elif target_function == 'amp':
            if param in inspect.getfullargspec(self.amp_function)[0]:
                if value != self.amp_params[param]:
                    self.amp_params[param] = value
                    self.equalise_param_lengths()
                    self.needs_to_calculate = True
            else:
                raise NameError('{} is not a parameter for amp_{} function'.format(param,self.amp_function_name))
    
    def update_param_single_tone(self,param,value,tone_index,target_function=None):
        """Updates the relvant function dictionary with a new parameter value
        for a single tone. This is the method that is called when PyDex sends 
        a TCP message to the `MainWindow` to update a parameter.
        
        Parameters
        ----------
        param : str
            The function kwarg to change.
        value : float
            The value to change the arguement to.
        tone_index : int
            The index of the tone to change. This should be a non-negative 
            integer if only one tone should be changed. If a negative integer 
            is supplied, all tones will be changed to the given value.
        target_function : {'freq','amp',None}
            The target function to update the parameter of (either the 
            frequency or amplitude function). If not 'freq' or 'amp', the freq 
            and amp param dictonaries will be searched for the parameter and 
            the correct one will be modified, with preference given to the 
            freq dict. The default is None.
        """
        if param == 'duration_ms':
            self.duration_ms = value
            self.calculate_time()
            self.needs_to_calculate = True
            return
        elif param == 'phase_behaviour':
            if value in ['optimise','continue','manual']:
                self.phase_behaviour = value
                self.needs_to_calculate = True
            else:
                logging.error('{} is not a valid value for {}. '
                              'Ignoring.'.format(value,param))
            return         
        
        if target_function not in ['freq','amp']:
            if param in inspect.getfullargspec(self.freq_function)[0]:
                target_function = 'freq'
            elif param in inspect.getfullargspec(self.amp_function)[0]:
                target_function = 'amp'
            else:
                logging.error('Parameter {} is not valid for either the '
                              'frequency or amplitude of this action. Nothing '
                              'will be changed.'.format(param))
                return
        
        if target_function == 'freq':
            if param not in inspect.getfullargspec(self.freq_function)[0]:
                logging.error('{} is not a parameter for freq_{} '
                              'function'.format(param,self.freq_function_name))
                return
            if tone_index < 0:
                new_values = [value]*len(self.freq_params[param])
            else:
                new_values = self.freq_params[param].copy()
                try:
                    new_values[tone_index] = value
                except IndexError:
                    logging.error('tone_index {} is out of range. Ignoring.'
                                  ''.format(tone_index))
                    return
        else:
            if param not in inspect.getfullargspec(self.amp_function)[0]:
                logging.error('{} is not a parameter for amp_{} '
                              'function'.format(param,self.amp_function_name))
                return
            if tone_index < 0:
                new_values = [value]*len(self.amp_params[param])
            else:
                new_values = self.amp_params[param].copy()
                try:
                    new_values[tone_index] = value
                except IndexError:
                    logging.error('tone_index {} is out of range. Ignoring.'
                                  ''.format(tone_index))
                    return
        
        self.update_param(target_function,param,new_values)
    
    def equalise_param_lengths(self):
        """Removes redundant data for when some kwargs are specified to be for
        more tones than others.
        
        Sets the length of all parameter value lists to that of the shortest
        list. Any lists that are longer length that than the shortest list are
        truncated and redundant data is deleted.
        

        Returns
        -------
        None.

        """
        min_length = max_tone_num
        for key in self.freq_params.keys():
            min_length = np.min([min_length,len(self.freq_params[key])])
        for key in self.amp_params.keys():
            min_length = np.min([min_length,len(self.amp_params[key])])
        
        for key in self.freq_params.keys():
            self.freq_params[key] = self.freq_params[key][:min_length]
        for key in self.amp_params.keys():
            self.amp_params[key] = self.amp_params[key][:min_length]

    def calculate_time(self):
        """Generates a `numpy` array containing the time steps (in ms) for the 
        action. This array starts from zero and runs to the `duration_ms` 
        attribute.
        
        The time is slightly modified to ensure that it contains the correct 
        number of points in the segment. This should be a multiple of 
        card_settings['segment_step_samples'].+ 1 (as the first point of all 
        calculated data is dropped when sending the sample to the card to avoid
        repeating the same time point twice.)
        
        The time is also set longer than the minimum segment time in 
        card_settings['segment_min_samples'].

        Returns
        -------
        None. Array is stored as `time` attribute within the class.

        """
        time_step = 1/self.card_settings['sample_rate_Hz']
        num_samples = round(self.duration_ms*1e-3*self.card_settings['sample_rate_Hz']/self.card_settings['segment_step_samples'])*self.card_settings['segment_step_samples']
        if num_samples < self.card_settings['segment_min_samples']:
            logging.error('Requested duration {} ms has too few samples to '
                          'make a valid sample. Setting number of samples '
                          'minimum {}'.format(self.duration_ms,
                                              self.card_settings['segment_min_samples']))
            num_samples = self.card_settings['segment_min_samples']
        num_samples = int(num_samples)
        self.duration_ms = num_samples*time_step*1e3
        self.time = np.linspace(0,self.duration_ms*1e-3,num_samples+1)
        self.data = np.empty_like(self.time)

    def calculate(self):
        """
        Calculates the action data to send to the AWG. Data is only 
        regenerated if the boolean attribute `needs_to_calculate` is True.
        
        The first data point of the data is dropped to ensure phase continuity 
        with the previous segment.

        Returns
        -------
        None. Calculated `numpy` array containing the data to send to the AWG 
        is stored in the attribute `data`.

        """
        
        # TODO: Currently all amplitude data is scaled by 1e-9 just to be safe
        #       but this should evenutally be removed.
        
        if self.needs_to_calculate:
            self.data = np.zeros_like(self.time)
            self.end_phase = []
            
            for tone_freq_params,tone_amp_params in zip(self.transpose_params(self.freq_params),self.transpose_params(self.amp_params)):
                freq_data = self.freq_function(**tone_freq_params)
                amp_data = self.amp_function(**tone_amp_params)
                
                phase_data = self.calculate_phase(freq_data,tone_freq_params['start_phase'])
                amp_data_mV = self.amp_adjuster.adjuster(freq_data,amp_data)
                
                self.data += amp_data_mV*np.sin(phase_data*2*np.pi/360)#*1e-9
                
                self.end_phase.append(phase_data[-1]%360)
            self.data = self.data[1:]
            self.needs_to_calculate = False
            self.needs_to_transfer = True
    
    def set_start_phase(self,phase=None):
        """Set the start phases to use when calculating the segment. Extra 
        phases will be discarded and new phases will be added if needed.
        
        The available options are described in the phase_behaviour attribute 
        in the class docstring.
        
        Parameters
        ----------
        phase : list of float or None
            The phases to set to if using 'continue' mode. This parameter will 
            be ignored in other modes. If set to None 'continue' mode will not 
            be used and the existing phases will be used instead. The default 
            is None.

        """       
        if self.phase_behaviour == 'optimise':
            self.freq_params['start_phase'] = phase_minimise(self.freq_params['start_freq_MHz'],self.amp_params['start_amp'])
            self.needs_to_calculate = True
            self.needs_to_transfer = True
        elif self.phase_behaviour == 'continue':
            if phase == None:
                return
            if len(phase) == len(self.freq_params['start_phase']):
                self.freq_params['start_phase'] = phase
            elif len(phase) < len(self.freq_params['start_phase']):
                self.freq_params['start_phase'] = len(phase) + [0]*(len(self.freq_params['start_phase'])-len(phase))
            else:
                self.freq_params['start_phase'] = phase[0:len(self.freq_params['start_phase'])]
            self.needs_to_calculate = True
            self.needs_to_transfer = True
    
    def get_end_phase(self):
        """Returns the final phase that this action ends on. For this to be 
        calculated, the entire action data is calculated and then the final
        phase is returned. The phase is returned in degrees in the range 
        0 - 360.        

        Returns
        -------
        list of float
            The final phases of the tones in the action, in radians.

        """
        self.calculate()
        return self.end_phase

    def calculate_freq(self,freq_params):
        """Calculates the frequency profile with the kwargs in the arguement
        `freq_params`. This dictionary should be the kwargs for a single tone 
        (i.e. the function `self.transpose_params` should be used before 
        passing arguements to this function).
        
        Parameters
        ----------
        freq_params : dict
            dict containing kwargs for a single tone frequency profile

        Returns
        -------
        array
            `numpy` array containing the amplitude data for the single tone.

        """
        return self.freq_function(**freq_params)

    def calculate_amp(self,amp_params):
        """Calculates the frequency profile with the kwargs in the arguement
        `amp_params`. This dictionary should be the kwargs for a single tone 
        (i.e. the function `self.transpose_params should be used before 
        passing arguements to this function).
        
        
        Parameters
        ----------
        amp_params : dict
            dict containing kwargs for a single tone amplitude profile

        Returns
        -------
        array
            `numpy` array containing the amplitude data for the single tone.

        """
        return self.amp_function(**amp_params)
        
    def calculate_phase(self,freq_data,initial_phase): 
        """Integrates the frequency profile of a single tone with time to 
        calculate its phase profile each time.
        
        Frequencies are defined up to this point in MHz, so this function 
        converts into Hz.
        

        Parameters
        ----------
        freq_data : array
            `numpy` array containing the frequency of the tone for each 
            timestep contained in the `time` attribute of the action.
        initial_phase : float
            The initial phase of the frequency tone, in radians.

        Returns
        -------
        phases : array
            `numpy` array containing the phase data for the tone. The phase is
            returned in degrees.

        """
        phases = []
        phase = initial_phase

        for i,cur_freq in enumerate(freq_data):
            phases.append(phase)
            if i < len(self.time)-1:
                phase += 360*cur_freq*1e6*(self.time[i+1]-self.time[i])

        phases = np.asarray(phases)
        return phases
    
    def get_autoplot_traces(self,num_points=50,show_amp_in_mV=True):
        """Returns samples of the amplitude and frequency profiles for the 
        autoplotter to use. Doesn't return the complete profile to make the
        plotting more lightweight.

        Parameters
        ----------
        num_points : int, optional
            The number of sample points to return for the frequency and 
            amplitude profiles. The default is 50.

        Returns
        -------
        freq_profiles : list of `numpy` arrays
            List of `numpy` arrays where each array is the frequency profile 
            for one tone of the action.
        amp_profiles : list of `numpy` arrays
            List of `numpy` arrays where each array is the amplitude profile 
            for one tone of the action.
        
        """

        idx = np.round(np.linspace(0, len(self.time) - 1, num_points)).astype(int)
        time = self.time[idx]
        
        freq_profiles = []
        amp_profiles = []
        
        for profile_freq_params,profile_amp_params in zip(self.transpose_params(self.freq_params),self.transpose_params(self.amp_params)):
            freq_profiles.append(self.freq_function(**profile_freq_params,_time=time))
            amp_profiles.append(self.amp_function(**profile_amp_params,_time=time))
        
        if show_amp_in_mV:
            for i, (freq_profile,amp_profile) in enumerate(zip(freq_profiles,amp_profiles)):
                amp_profiles[i] = self.amp_adjuster.adjuster(freq_profile,amp_profile)
                
        return freq_profiles, amp_profiles
    
    def transpose_params(self,params):
        """Converts the `params` dictionary from being a dictionary of lists to 
        a list of dictionaries, which is more useful when iterating over
        multiple tones of the action, as each tone can be calculated 
        seperately.
        
        Parameters
        ----------
        params : dict of lists
            A dictionary of kwargs for either the frequency or amplitude 
            function of the action. Each dictionary entry should contain a list
            of the kwarg value for the different tones of the action.

        Returns
        -------
        list of dicts
            A list where each entry is the kwarg dictionary for either the 
            frequency or amplitude function for one tone of the action.

        """
        return [dict(zip(params,t)) for t in zip(*params.values())]
    
    def is_freq_changing(self):
        """Helper function to determine if this segment changes (or is allowed 
        to change) the frequency of the AWG. Decides this based on if the key 
        `end_freq_MHz` is in the freq_params dict.
        
        Returns
        -------
        bool
            Whether the action can change the frequency of the AWG or not.
        
        """
        
        return 'end_freq_MHz' in self.freq_params.keys()
    
    def is_amp_changing(self):
        """Helper function to determine if this segment changes (or is allowed 
        to change) the amplitude of the AWG. Decides this based on if the key 
        `end_amp` is in the amp_params dict.
        
        Returns
        -------
        bool
            Whether the action can change the amplitude of the AWG or not.
        
        """
        
        return 'end_amp' in self.amp_params.keys()
    
    def is_static(self):
        """Helper function to return whether this action is static in both 
        frequency and amplitude.
        
        This function is used to define whether this action can be set to loop 
        or not, and for deciding priority when adjusting frequencies.
        
        Returns
        -------
        bool
            Whether this action is static in both frequency and amplitude.
            
        """
        return (self.freq_function_name == 'static') and (self.amp_function_name == 'static')
        
    """   
    When adding new functions, use start_freq_MHz and start_amp even 
    if the freq/amp remains constant so that the enforced freq/amp 
    continuity is used in the GUI.
    
    All frequency functions should include the kwarg `start_phase` even though
    this is not used in the calculation of the frequency profile; this is later
    used when calculating the phase profile of the tones.
    
    All functions should have the kwarg `_time` with default value None. This 
    will not show up in the GUI but allows smaller subsets of the action to be
    calculated rather than the entire action (e.g. when autoplotting).

    Global params (e.g. duration, sample rate, etc. should be passed as
    class attributes rather than function arguements.)
    """

    def freq_static(self,start_freq_MHz=100,start_phase=0,_time=None):
        if _time is None:
            _time = self.time
        return np.ones_like(_time)*start_freq_MHz

    def freq_sweep(self,start_freq_MHz=100,end_freq_MHz=101,hybridicity=1,
                   start_phase=0,_time=None):
        if _time is None:
            _time = self.time
        if hybridicity == 1:
            return np.linspace(start_freq_MHz,end_freq_MHz,len(_time))
        elif hybridicity == 0:
            return self.freq_min_jerk(start_freq_MHz,end_freq_MHz,start_phase,_time)
        else:
            return self.freq_min_jerk(start_freq_MHz,end_freq_MHz,start_phase,_time)
            #TODO finish this
            d = (end_freq_MHz-start_freq_MHz)
            T = _time[-1] - _time[0]
            deltat = T*(1-hybridicity)/2
            deltaf = d/(2+15/4*hybridicity/(1-hybridicity))
            time1 = _time[0:round((1-hybridicity)/2)]
            time2 = _time[round((1-hybridicity)/2)]
            
            # freq1 = list(freq_min_jerk(start_freq_MHz,start_freq_MHz+2*deltaf,))
            
    def freq_min_jerk(self,start_freq_MHz=100,end_freq_MHz=101,start_phase=0,_time=None):
        if _time is None:
            _time = self.time
        d = (end_freq_MHz-start_freq_MHz)
        T = _time[-1] - _time[0]
        return d*(10*(_time/T)**3 - 15*(_time/T)**4 + 6*(_time/T)**5) + start_freq_MHz
    
    def amp_static(self,start_amp=1,_time=None):
        if _time is None:
            _time = self.time
        return np.ones_like(_time)*start_amp

    def amp_ramp(self,start_amp=1,end_amp=0,_time=None):
        if _time is None:
            _time = self.time
        return np.linspace(start_amp,end_amp,len(_time))
    
    def amp_drop(self,drop_time_us=100,start_amp=1,drop_amp=0,_time=None):
        if _time is None:
            _time = self.time
        amp = np.ones_like(_time)*start_amp
        duration = _time[-1]
        drop_idx = np.where(np.abs(_time-duration/2)<((drop_time_us*1e-6)/2))
        amp[drop_idx] = drop_amp
        return amp
    
if __name__ == '__main__':
    card_settings = {'active_channels':1,
                     'sample_rate_Hz':625000000,
                     'max_output_mV':100,
                     'number_of_segments':8,
                     'segment_min_samples':192,
                     'segment_step_samples':32
                     }
    action_params = {'duration_ms' : 1,
                     'freq' : {'function' : 'static',
                               'start_freq_MHz': [100],
                               'start_phase' : [0]},
                     'amp' : {'function' : 'static',
                              'start_amp': [1]}}
    action = ActionContainer(action_params,card_settings,None)