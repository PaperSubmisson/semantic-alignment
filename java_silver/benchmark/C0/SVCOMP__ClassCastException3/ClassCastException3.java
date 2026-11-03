class A {}

class B {}

public class ClassCastException3 {
  public static boolean f() {
    try {
      Object a = new A();
      B b = (B) a;
    } catch (Exception exc) {
      return false;
    }
    return true;
  }
}
