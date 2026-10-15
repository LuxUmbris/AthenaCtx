enum Color {
    Red,
    Green = 2,
}

enum Payload {
    Number(i64),
    Text(String),
}

union Raw {
    number: u32,
    bits: f32,
}

struct Point {
    x: i32,
    y: i32,
}

impl Point {
    fn sum(&self) -> i32 {
        helper(self.x) + self.y
    }
}

fn helper(value: i32) -> i32 {
    value
}
