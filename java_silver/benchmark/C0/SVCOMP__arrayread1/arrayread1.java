class arrayread1 {

  static arrayread1 readback;

  public static int func(int c) {
    if (c != 1) return -1;
    arrayread1[] a = new arrayread1[c];
    readback = a[0];
    return ((readback == null) ? 1 : 0);
  }
}