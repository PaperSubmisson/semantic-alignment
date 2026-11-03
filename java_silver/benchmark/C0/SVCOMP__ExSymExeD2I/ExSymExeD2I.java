class Main {

  public static boolean f (double x) {
    if (x >= 0.0 && x <= Integer.MAX_VALUE / 2) {
      Main inst = new Main();
      return inst.test(x);
    }
    return true;
  }

  public boolean test(double x) {

    int res = (int) ++x;
    if (res > 0) System.out.println("x >0");
    else {
      return false;
    }
    return true;
  }
}
