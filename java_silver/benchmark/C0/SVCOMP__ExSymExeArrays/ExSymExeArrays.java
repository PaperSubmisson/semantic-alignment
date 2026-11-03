class Main {
  static int[] a;

  public static boolean f() {
    a = new int[1];
    int x = -3;

    Main inst = new Main();

    return inst.test(x);
  }

  public boolean test(int x) {
    a[0] = x;
    if (a[0] >= 0) {
      return false;
    } else System.out.println("branch2 <0");
    return true;
  }
}
