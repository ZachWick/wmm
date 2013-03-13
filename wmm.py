#!/usr/bin/python2
'''
wmm.py
Copyright ZachWick <zach@zachwick.com>
Licensed GPLv3

WMM - (W)indow (M)anager (M)inus
       A minimalist window manager that aims to small and quick with a minimal 
       feature set.

Mod4 + Enter opens a new Xterm

'''

import os
import sys
import traceback

import Xlib.rdb, Xlib.X, Xlib.XK

REQUIRED_XLIB_VERSION = (0,14)
MAX_EXCEPTIONS = 25
XTERM_COMMAND = ['/usr/bin/xterm']

RELEASE_MODIFIER = Xlib.X.AnyModifier << 1

class NoUnmanagedScreens(Exception):
    pass

class WM(object):
    def __init__(self, display):
        self.display = display
        self.max_width = self.display.screen(None).width_in_pixels
        self.max_height = self.display.screen(None).height_in_pixels
        self.drag_window = None
        self.drag_offset = (0, 0)

        if display is not None:
            os.environ['DISPLAY'] = display.get_display_name()
        self.enter_codes = set(code for code, index in self.display.keysym_to_keycodes(Xlib.XK.XK_Return))
        self.p_codes = set(code for code, index in self.display.keysym_to_keycodes(Xlib.XK.XK_p))

        self.screens = []
        for screen_id in xrange(0, display.screen_count()):
            if self.redirect_screen_events(screen_id):
                self.screens.append(screen_id)

        if len(self.screens) == 0:
            raise NoUnmanagedScreens()

        self.display.set_error_handler(self.x_error_handler)

        self.event_dispatch_table = {
            Xlib.X.MapRequest: self.handle_map_request,
            Xlib.X.ConfigureRequest: self.handle_configure_request,
            Xlib.X.MappingNotify: self.handle_mapping_notify,
            Xlib.X.MotionNotify: self.handle_mouse_motion,
            Xlib.X.ButtonPress: self.handle_mouse_press,
            Xlib.X.ButtonRelease: self.handle_mouse_release,
            Xlib.X.KeyPress: self.handle_key_press,
            Xlib.X.KeyRelease: self.handle_key_release,
        }

    def redirect_screen_events(self, screen_id):
        '''
        Attemps to redirect the screen events, and returns True on success.
        '''
        root_window = self.display.screen(screen_id).root

        error_catcher = Xlib.error.CatchError(Xlib.error.BadAccess)
        mask = Xlib.X.SubstructureRedirectMask
        root_window.change_attributes(event_mask=mask, onerror=error_catcher)

        self.display.sync()
        error = error_catcher.get_error()
        if error:
            return False

        for code in self.enter_codes:
            # Grab Mod1 + Enter
            root_window.grab_key(code,
                                 Xlib.X.Mod1Mask & ~RELEASE_MODIFIER,
                                 1,
                                 Xlib.X.GrabModeAsync,
                                 Xlib.X.GrabModeAsync)

        # Find all existing windows
        for window in root_window.query_tree().children:
            # Get mouse motion events for this window
            self.grab_window_events(window)

        return True

    def x_error_handler(self, err, request):
        print >> sys.stderr, 'X protocol error: {0}'.format(err)

    def main_loop(self):
        '''
        Loop until Ctrl+c or more exceptions than MAX_EXCEPTION have occurred
        '''
        errors = 0
        while True:
            try:
                self.handle_event()
            except (KeyboardInterrupt, SystemExit):
                raise
            except:
                errors += 1
                if errors > MAX_EXCEPTIONS:
                    raise
                traceback.print_exc()

    def handle_event(self):
        '''
        Wait for the next event and then handle it.
        '''
        try:
            event = self.display.next_event()
        except Xlib.error.ConnectionClosedError:
            print >> sys.stderr, 'Display connection closed by server'
            raise KeyboardInterrupt

        if event.type in self.event_dispatch_table:
            handler = self.event_dispatch_table[event.type]
            handler(event)
        else:
            print 'unhandled event: {event}'.format(event=event)

    def handle_configure_request(self, event):
        window = event.window
        args = { 'border_width': 0 }
        if event.value_mask & Xlib.X.CWX:
            args['x'] = 0
        if event.value_mask & Xlib.X.CWY:
            args['y'] = 0
        if event.value_mask & Xlib.X.CWWidth:
            args['width'] = self.max_width
        if event.value_mask & Xlib.X.CWHeight:
            args['height'] = self.max_height
        if event.value_mask & Xlib.X.CWSibling:
            args['sibling'] = event.above
        if event.value_mask & Xlib.X.CWStackMode:
            args['stack_mode'] = event.stack_mode
        window.configure(**args)

    def handle_map_request(self, event):
        event.window.map()
        self.grab_window_events(event.window)

    def grab_window_events(self, window):
        '''
        Grab right-click and right-drag events on the window.
        '''
        window.grab_button(3, 0, True,
                           Xlib.X.ButtonMotionMask | Xlib.X.ButtonReleaseMask | Xlib.X.ButtonPressMask,
                           Xlib.X.GrabModeAsync,
                           Xlib.X.GrabModeAsync,
                           Xlib.X.NONE,
                           Xlib.X.NONE,
                           None)

    def handle_mapping_notify(self, event):
        self.display.refresh_keyboard_mapping(event)

    def handle_mouse_motion(self, event):
        '''
        Right-click & drag to move window
        '''
        if event.state & Xlib.X.Button3MotionMask:
            if self.drag_window is None:
                # Start right-drag
                self.drag_window = event.window
                g = self.drag_window.get_geometry()
                self.drag_offset = g.x - event.root_x, g.y - event.root_y
            else:
                # Continue the right-drag
                x, y = self.drag_offset
                self.drag_window.configure(x=x + event.root_x, y=y + event.root_y)

    def handle_mouse_press(self, event):
        if event.detail == 3:
            # Right-click: raise window
            event.window.configure(stack_mode=Xlib.X.Above)

    def handle_mouse_release(self, event):
        self.drag_window = None

    def handle_key_press(self, event):
        if event.state & Xlib.X.Mod1Mask and event.detail in self.enter_codes:
            # Mod1 + Enter: start xterm
            self.system(XTERM_COMMAND)

    def handle_key_release(self, event):
        pass

    def system(self, command):
        '''
        Forks a command and then disowns it.
        '''
        if os.fork() != 0:
            return

        try:
            # Child
            os.setsid() # Become a session leader
            if os.fork() != 0:
                os._exit(0)

            os.chdir(os.path.expanduser('~'))
            os.umask(0)

            # Close all file descriptors
            import resource
            maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
            if maxfd == resource.RLIM_INFINITY:
                maxfd = 1024
            for fd in xrange(maxfd):
                try:
                    os.close(fd)
                except OSError:
                    pass

            # Open /dev/null for stdin, stdout, stderr.
            os.open('/dev/null',os.O_RDWR)
            os.dup2(0, 1)
            os.dup2(0, 2)

            os.execve(command[0], command, os.environ)
        except:
            try:
                # Error in child process
                print >> sys.stderr, 'Error in child process:'
                traceback.print_exc()
            except:
                pass
            sys.exit(1)
def main():
    if Xlib.__version__ < REQUIRED_XLIB_VERSION:
        print >> sys.stderr, 'Xlib version 0.14 is required, {ver} was found'.format(ver='.'.join(str(i) for i in Xlib.__version__))
        return 2

    display, appname, resource_database, args = Xlib.rdb.get_display_opts(Xlib.rdb.stdopts)

    try:
        wm = WM(display)
    except NoUnmanagedScreens:
        print >> sys.stderr, 'No unmanaged screens found'
        return 2

    try:
        wm.main_loop()
    except KeyboardInterrupt:
        print
        return 0
    except SystemExit:
        raise
    except:
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())
