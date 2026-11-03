class classB {
  public static int some_method() {
    return 123;
  }
}

class Main {
  public static boolean f() {
    int result = classB.some_method();
    return result == 123;
  }
}
;
