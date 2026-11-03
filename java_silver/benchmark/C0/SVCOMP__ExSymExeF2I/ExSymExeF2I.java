class Main {

  public static boolean f(float x) {
    if (x >= 0.0f && x <= Integer.MAX_VALUE / 2) {
      Main inst = new Main();
      return inst.test(x);
    }
    return true;
  }

  public boolean test(float x) {

    int res = (int) ++x;
    if (res > 0) System.out.println("x >0");
    else {
      return false;
    }
    return true;
  }
}
