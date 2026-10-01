import rtmidi
# Create MidiIn and MidiOut objects
midi_in = rtmidi.MidiIn()
midi_out = rtmidi.MidiOut()
# Get available MIDI input and output ports
input_ports = midi_in.get_ports()
output_ports = midi_out.get_ports()
# Print the available MIDI devices
print("Available MIDI Input Ports:")
for port in input_ports:    
    print(port)
print("\nAvailable MIDI Output Ports:")
for port in output_ports:    
    print(port)