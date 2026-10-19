#[cfg(not(target_arch = "wasm32"))]
fn main() {
    println!("Only WASM target is supported in this stripped version.");
}
