enum Color {
    RED,
    GREEN = 2,
};

union Value {
    int number;
    float ratio;
};

int add(int lhs, int rhs) {
    return lhs + rhs;
}
