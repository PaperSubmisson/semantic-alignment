class A {
  int i;
}

public class NullPointerException3 {
  public static boolean f() {
    A a = null;
    try {
      int i = a.i;
    } catch (NullPointerException exc) {
      return false;
    }
    return true;
  }
}
