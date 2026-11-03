class Main {
  static int field;

  public static boolean f() {
    int x = 3;
    Main inst = new Main();
    field = 9;
    return inst.test2(0.0);
  }

  public boolean test2(double in) {
    if (Math.round(in) > 16.0) {
      return false;
    } else
      System.out.println("do2()");
    return true;
  }

  public void angleXY_(double x1, double y1) {
    double x = x1;
    double y = y1;
    if (x == 0 && y != 0) {
      System.out.println(">>>>>>>>>>>> 1");
    } else {
      System.out.println(">>>>>>>>>>>> LOOK!");
      if (x < 0) {
        System.out.println(">>>>>>>>>>>>>>> ???");
      } else if (x > 0) {
        System.out.println(">>>>>>>>>>>>>>> !!!1");
      }
    }
  }
}
