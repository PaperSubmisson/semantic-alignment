public class ClassCastException1 {
  public static boolean f() {
    try {
      Object x = new Integer(0);
      String y = (String) x;
    } catch (ClassCastException exc) {
      return false;
    }
    return true;
  }
}
