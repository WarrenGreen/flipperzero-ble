// Relay the GPIO serial pins (USART, header pins 13 TX / 14 RX) to a file the SDK can read back.
//
// The Flipper's RPC protocol can't reach a module on the serial pins, but a script can. Run it with
// flipper.scripts.run_and_read(Path("examples/serial_relay.js"), "/ext/.serial_out.txt", seconds=8).
//
// Written for the Flipper's mJS engine: while-loops (not for), and the file is flushed line by line so a
// forced close still leaves the captured data on disk. Change the baud and the nudge for your module
// (e.g. a WiFi board running Marauder answers "help\n" at 115200).
let storage = require("storage");
let OUT = "/ext/.serial_out.txt";
function logline(s) { let fh = storage.openFile(OUT, "w", "open_append"); fh.write(s + "\n"); fh.close(); }
function hexof(chunk) {
    if (chunk === undefined) return "none";
    let view = Uint8Array(chunk);
    let hex = ""; let k = 0;
    while (k < view.length) { let b = view[k]; hex += (b < 16 ? "0" : "") + b.toString(16); k++; }
    return hex;
}

let f0 = storage.openFile(OUT, "w", "create_always"); f0.write("start\n"); f0.close();
let serial = require("serial");

serial.setup("usart", 115200);
serial.write("help\n");
let t = 0;
while (t < 40) {              // ~4 s of 100 ms reads
    logline("rx " + hexof(serial.readAny(100)));
    t++;
}
serial.end();
logline("done");
print("done");
