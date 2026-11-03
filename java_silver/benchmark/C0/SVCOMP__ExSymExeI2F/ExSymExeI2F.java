class Main {

  public static boolean f(int x) {
    if (x != 3 && x != 0)
      return true;

    Main inst = new Main();
    return inst.test(x);
  }

  public boolean test(int x) {
    float res = (float) ++x;
    if (res > 0) System.out.println("x >0");
    else {
      return false;
    }
    return true;
  }
}
