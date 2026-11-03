class Main {
  static int field;

  public static boolean f(int arg) {
    if (arg < 0)
      return true;
    int x = 3;
    Main inst = new Main();
    field = arg % 100;
    return inst.test(x, field);
  }
  
  public boolean test(int x, int z) {
    System.out.println("Testing ExSymExe11");
    int y = 3;
    z = -x;
    x = z * x;
    if (z <= 0) System.out.println("branch FOO1");
    else System.out.println("branch FOO2");
    if (y <= 0) {
      System.out.println("branch BOO1");
      return false;
    } else System.out.println("branch BOO2");
    return true;
  }
}
