import logging
from .client import PyClient
from .server import PyServer

class Networker():
    """Handles the PyClient to recieve TCP messages from Dexter before calling 
    the correct method of the main_window class.
    
    Also handles the PyServer to respond to PyDex when a response is needed 
    such as when loading in files from a TCP command.
    
    Attributes
    ----------
    client : PyClient
        The client to recieve the TCP messages from PyDex.
    server : PyServer
        The server to respond to PyDex when needed.
    
    """
    
    def __init__(self,main_window,client_ip,client_port,server_ip,server_port):
        """Defines the class, including setting up the TCP server.

        Parameters
        ----------
        main_window : MainWindow
            The MainWindow class that is the parent of this object. Passing 
            through this reference allows the networker to call the relevant 
            methods of the MainWindow when the correct TCP message is 
            recieved.
        client_ip : str
            The IP address for the client to listen for messages from. This 
            should typically be the address of the PyDex PC.
        client_port : int
            The port for the client to listen for messages from. This 
            should typically be the port that the PyDex PC is using.
        server_ip : str
            The IP address for the server to send messages from. This should 
            typically be ''.
        server_port : int
            The port for the client to listen for messages from. This 
            should typically be the port that the PyDex PC is using.
        client_name : str
            The name of the TCP server.

        Returns
        -------
        None.

        """
        
        self.main_window = main_window
        
        self.client = PyClient(host=client_ip,port=int(client_port))
        self.client.start()
        self.client.textin.connect(self.recieved_tcp_msg)
        
        self.server = PyServer(host=server_ip, port=int(server_port)) # TCP server to message PyDex
        self.server.start()
        
    def recieved_tcp_msg(self,msg):
        """Takes the recieved TCP messages and performs the action that 
        corresponds to that TCP message.
        
        Accepted commands (in order of evaluation, * is a wildcard)
        -----------------------------------------------------------
        *load* = filename
            Loads the AWGparams file located in the path defined by filename 
            (str) into the interface. It then sends this data to the card.
        *save* = filename
            Saves the AWGparams loaded into the interface to the path defined 
            by filename (str). Note that the parameters in the interface are 
            saved; if these have been changed without updating the card, these
            might not be the settings currently on the card!
        *trigger* = None
            Forces a trigger on the AWG card.
        *rearrange* = rearrange_occupancy (e.g. 001001)
            Triggers the segment of the rearrangement step to be updated based 
            on the rearrange_occupancy (binary string)
        *set_data* = [[channel (int), segment (int), param (str), value (float),
                      tone_index (int)],...]
            Updates the parameter of a given action with the supplied value. A
            list of lists is recieved from PyDex to allow more than one 
            setting to be changed without having to send multiple TCP messages.
        *set_complete_data* = [channel (int), segment(int), param (str), 
                               values (list)]
            Sets the complete value list of an action. The length of the other
            settings in the action are set to the length of this list.
        
        Other commands are ignored.
        
        """
        # import time
        # start = time.time()
        msg = msg.replace('#','')
        logging.info('TCP message recieved: "'+msg+'"')
        try:
            split_msg = msg.split('=')
            command = split_msg[0]
            arg = split_msg[1]
        except:
            logging.error("Could not parse command TCP message '{}'. Message ignored.".format(msg))
            return
        
        if 'rearrange' in command:
            if all(x in '01' for x in arg):
                logging.info("Rearrangement string '{}' recieved.".format(arg))
                self.main_window.rearr_recieve(arg)
                # print('time',time.time()-start)
            else:
                logging.error("Invalid rearrangement string '{}' recieved. Message ignored.".format(arg))
                return
        elif 'set_data' in command:
            try:
                arg = eval(arg)
                self.main_window.data_recieve(arg)
            except NameError:
                logging.error("NameError in data string '{}' (the param name must be contained in ''). Message ignored.".format(arg))
            except SyntaxError:
                logging.error("SyntaxError in data string '{}'. Message ignored.".format(arg))
            self.server.add_message(1,'go'*1000)
        elif 'set_complete_data' in command:
            try:
                arg = eval(arg)
                self.main_window.complete_data_recieve(arg)
            except NameError:
                logging.error("NameError in data string '{}' (the param name must be contained in ''). Message ignored.".format(arg))
            except SyntaxError:
                logging.error("SyntaxError in data string '{}'. Message ignored.".format(arg))
            self.server.add_message(1,'go'*1000)
        elif 'load' in command:
            filename_stripped = arg.split('.')[0]
            self.main_window.load_params(filename_stripped+'.awg')
            self.main_window.load_rearr_params(filename_stripped+'.awgrr')
            self.main_window.calculate_send()
        elif 'save' in command:
            filename_stripped = arg.split('.')[0]
            self.main_window.save_params(filename_stripped+'.awg')
            self.main_window.rr.save_params(filename_stripped+'.awgrr')
        elif 'trigger' in command:
            logging.info('Triggering AWG as requested by TCP command.')
            self.main_window.awg.trigger()
        elif 'rearrange' in command:
            if all(x in '01' for x in arg):
                logging.info("Rearrangement string '{}' recieved.".format(arg))
                self.main_window.rearr_recieve(arg)
                # print('time',time.time()-start)
            else:
                logging.error("Invalid rearrangement string '{}' recieved. Message ignored.".format(arg))
                return
        else:
            logging.error("Command '{}' not recognised. Ignoring TCP message.".format(command))
            return