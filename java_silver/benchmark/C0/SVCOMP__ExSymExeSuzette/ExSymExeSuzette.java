class Main {
  public boolean test(int x, int y) {

    int v = method_a(x, y);

    if (v > 0) {
      return false;
    }
    return true;
  }

  public int method_a(int x, int y) {

    if (x > 10) return x;

    if (y > 10) return y;

    return 0;
  }

  public int method_b(int z) {

    if (z > 10) return z++;
    else return z--;
  }

  public static boolean f(int arg) {

    Main ex = new Main();
    if (arg < 0 || arg > 10) return true;
    return ex.test(arg, 0);
  }
}
