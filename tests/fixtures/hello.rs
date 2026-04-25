use std::env;

fn main() {
    let name = env::var("USER").unwrap_or_else(|_| "world".to_string());
    for i in 1..=3 {
        println!("hello {}! ({})", name, i);
    }
}
