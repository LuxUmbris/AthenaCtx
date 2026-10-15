export function add(a, b) {
  return a + b;
}

export class Box {
  value() {
    return add(2, 3);
  }
}
