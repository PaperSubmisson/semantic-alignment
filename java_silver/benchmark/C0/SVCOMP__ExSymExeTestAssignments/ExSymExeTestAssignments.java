class Main {
  int field;

  public static boolean f (int x) {
    if (x < 0) return true;
    return test(x);
  }

  public static boolean test(int x) {
    x = 3;
    if (x > 0) System.out.println("branch BOO1");
    else {
      return false;
    }
    return true;
  }
}
