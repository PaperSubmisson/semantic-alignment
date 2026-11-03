class A extends Exception {}

class athrow1 {
  public static boolean func() {
    A a = new A();
    try {
      throw a;
    } catch (Exception e) {
      return false;
    }
  }
}
