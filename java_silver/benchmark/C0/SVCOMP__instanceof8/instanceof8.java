class Main {
  public static boolean test(Integer i) {
    if (i instanceof Integer) {
      return true;
    } else {
      return false;
    }
  }

  public static boolean f() {
    return (!test(null)) && (test(new Integer(1)));
  }
}
