class A {
  int i;
}

public class NullPointerException4 {
  public static boolean f() {
    A a = null;
    try {
      a.i = 0;
    } catch (Exception exc) {
      return false;
    }
    return true;
  }
}
