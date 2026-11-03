class assert3 {

  public static void func() {
    int i = Verifier.nondetInt();

    if (i >= 1000)
      if (!(i > 1000))
        assert i == 1000;
  }
  
}