class NullPointerException1 {
  public static boolean f() {
    Object o = null;
    try {
      o.hashCode();
      // should pass
      return false;
    } catch (Exception e) {
    }
    return true;
  }
}
;
