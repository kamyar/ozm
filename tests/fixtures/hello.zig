const std = @import("std");

pub fn main() !void {
    const stdout = std.io.getStdOut().writer();
    const name = std.posix.getenv("USER") orelse "world";
    var i: u8 = 1;
    while (i <= 3) : (i += 1) {
        try stdout.print("hello {s}! ({d})\n", .{ name, i });
    }
}
