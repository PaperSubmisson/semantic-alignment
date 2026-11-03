class Main {

  public static boolean f(long x) {
    Main inst = new Main();
    return inst.test(x, 5);
  }

  public boolean test(long x, long y) {

    long res = x;
    if (res + 1 > res + 2) {
      return false;
    } else
      System.out.println("x <=0");
    return true;
  }
}
