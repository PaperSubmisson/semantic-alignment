class Main {
  static int field;

  public static boolean f() {
    int x = 3;
    Main inst = new Main();
    field = 9;
    return inst.test(x, field);
  }
  
  public boolean test(int x, int z) {
    System.out.println("Testing ExSymExe16");
    int y = 3;
    x = z - y;
    if (z == 0) {
      System.out.println("branch FOO1");
      return false;
    } else System.out.println("branch FOO2");
    if (x == 0) System.out.println("branch BOO1");
    else
      System.out.println("branch BOO2");
    return true;
  }
}
